---
title: End-to-End Langfuse Multi-Modal Implementation Reference
description: Deep-dive into how Langfuse implements multi-modal content collection from SDK instrumentation through storage, deduplication, UI rendering, and content reconstruction
author: Microsoft
ms.date: 2026-05-13
ms.topic: reference
keywords:
  - langfuse
  - multi-modal
  - implementation
  - SDK
  - media upload
  - S3
  - deduplication
  - content reconstruction
---

## Purpose

This document captures the end-to-end implementation details of how Langfuse handles non-text content (images, audio, PDFs) from agentic workloads. It traces the flow from SDK-level instrumentation through server-side storage to UI rendering and content reconstruction. This serves as a reference for designing Azure Monitor's multi-modal observability support (see [Research-Multi-Modal.md](Research-Multi-Modal.md)).

## Architecture Overview

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                        USER APPLICATION                                │
│                                                                        │
│  OpenAI / Anthropic / Vertex AI / Custom LLM calls                     │
│  with images, audio, PDFs in message content                           │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     LANGFUSE SDK LAYER                                  │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  1. Instrumentation Hooks (OpenAI wrapper / @observe decorator) │  │
│  │     - Intercept LLM call inputs and outputs                     │  │
│  │     - Call _process_message() to detect media in content parts  │  │
│  └──────────────────────────┬───────────────────────────────────────┘  │
│                             │                                          │
│  ┌──────────────────────────▼───────────────────────────────────────┐  │
│  │  2. MediaManager._find_and_process_media()                      │  │
│  │     - Recursive traversal of input/output data (max 10 levels)  │  │
│  │     - Pattern matching for base64, OpenAI, Anthropic, Vertex    │  │
│  │     - Wraps detected media in LangfuseMedia objects             │  │
│  └──────────────────────────┬───────────────────────────────────────┘  │
│                             │                                          │
│  ┌──────────────────────────▼───────────────────────────────────────┐  │
│  │  3. LangfuseMedia class                                         │  │
│  │     - Parses base64 data URI → content_bytes + content_type     │  │
│  │     - Computes SHA-256 hash → derives media_id (22 chars)       │  │
│  │     - Generates reference string to replace inline content      │  │
│  └──────────────────────────┬───────────────────────────────────────┘  │
│                             │                                          │
│  ┌──────────────────────────▼───────────────────────────────────────┐  │
│  │  4. Media Upload Queue (background thread)                      │  │
│  │     - Enqueues UploadMediaJob with bytes, hash, trace context   │  │
│  │     - Background consumer processes uploads non-blocking        │  │
│  │     - Exponential backoff with max 3 retries                    │  │
│  └──────────────────────────┬───────────────────────────────────────┘  │
│                             │                                          │
│  ┌──────────────────────────▼───────────────────────────────────────┐  │
│  │  5. Upload to Server                                            │  │
│  │     a. POST /api/public/media → get presigned upload URL        │  │
│  │     b. PUT {presigned_url} → upload binary to S3/Blob           │  │
│  │     c. PATCH /api/public/media/{id} → confirm upload status     │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│  Trace/span payload sent with reference tokens instead of binary       │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     LANGFUSE SERVER                                     │
│                                                                        │
│  PostgreSQL: media table (id, project_id, sha256, bucket_path, ...)    │
│  PostgreSQL: trace_media / observation_media junction tables           │
│  ClickHouse: trace and observation data with reference tokens          │
│  S3/Blob: raw binary media files                                       │
│                                                                        │
│  UI: detects reference tokens → fetches presigned download URL →       │
│      renders inline (images, audio player, PDF preview)                │
└─────────────────────────────────────────────────────────────────────────┘
```

## End-to-End Authentication Flow

This section details how authentication works across the entire media upload and download lifecycle — from the application SDK to object storage and from the UI back to object storage. The analysis is based on Langfuse source code in `apiAuth.ts`, `createAuthedProjectAPIRoute.ts`, `StorageService.ts`, `getMediaStorageClient.ts`, `media/index.ts`, and `media/[mediaId].ts`.

### Authentication Participants

```text
┌──────────────┐  ┌──────────────────┐  ┌──────────────┐  ┌──────────────────┐
│  Application │  │  Langfuse Server │  │  PostgreSQL  │  │  Object Storage  │
│  (SDK)       │  │  (Next.js API)   │  │  (API Keys)  │  │  (S3/Blob/GCS)   │
└──────────────┘  └──────────────────┘  └──────────────┘  └──────────────────┘

┌──────────────┐
│  Browser UI  │
│  (React App) │
└──────────────┘
```

There are **three distinct authentication domains** with **no direct credential sharing** between them:

| Domain | Credential Type | Who Holds It |
|--------|----------------|-------------|
| SDK → Langfuse Server | HTTP Basic Auth (`public_key:secret_key`) | Application developer |
| Langfuse Server → Object Storage | Cloud provider credentials (IAM / Access Keys) | Server operator |
| SDK/UI → Object Storage | Presigned URL (time-limited, scoped token embedded in URL) | Temporary, per-request |

### Upload Flow: Application SDK to Object Storage

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│  UPLOAD AUTHENTICATION FLOW                                                │
│                                                                            │
│  Step 1: SDK authenticates to Langfuse Server                              │
│  ──────────────────────────────────────────────                             │
│                                                                            │
│  SDK (Python/JS)                          Langfuse Server                  │
│  │                                          │                              │
│  │  POST /api/public/media                  │                              │
│  │  Authorization: Basic base64(pk:sk)      │                              │
│  │  Body: {contentType, sha256Hash,         │                              │
│  │         contentLength, traceId, field}   │                              │
│  │ ─────────────────────────────────────►   │                              │
│  │                                          │                              │
│  │                          ┌───────────────┤                              │
│  │                          │ ApiAuthService │                              │
│  │                          │ 1. Decode Basic auth header                  │
│  │                          │ 2. Extract public_key + secret_key           │
│  │                          │ 3. SHA-256 hash the secret_key + SALT        │
│  │                          │ 4. Lookup in Redis cache or PostgreSQL       │
│  │                          │ 5. Verify key → resolve projectId            │
│  │                          │ 6. Check rate limits                         │
│  │                          │ 7. Check ingestion not suspended             │
│  │                          └───────────────┤                              │
│  │                                          │                              │
│  Step 2: Server generates presigned upload URL using its own credentials   │
│  ───────────────────────────────────────────────────────────────────────    │
│  │                                          │                              │
│  │                          ┌───────────────┤                              │
│  │                          │ StorageService │                              │
│  │                          │ (S3Client with server-side IAM/keys)         │
│  │                          │                                              │
│  │                          │ getSignedUploadUrl({                         │
│  │                          │   path: "{prefix}{projectId}/{mediaId}.ext", │
│  │                          │   ttlSeconds: 3600,                          │
│  │                          │   sha256Hash, contentType, contentLength     │
│  │                          │ })                                           │
│  │                          │                                              │
│  │                          │ → PutObjectCommand signed with               │
│  │                          │   server's AWS credentials                   │
│  │                          │ → URL includes: signature, expiry,           │
│  │                          │   allowed content-type, content-length       │
│  │                          └───────────────┤                              │
│  │                                          │                              │
│  │  Response: {mediaId, uploadUrl}          │                              │
│  │ ◄─────────────────────────────────────   │                              │
│  │                                          │                              │
│  Step 3: SDK uploads directly to Object Storage (no server credentials)    │
│  ──────────────────────────────────────────────────────────────────────     │
│  │                                                                         │
│  │                                          Object Storage (S3/Blob/GCS)   │
│  │  PUT {presigned_url}                       │                            │
│  │  Content-Type: image/png                   │                            │
│  │  x-amz-checksum-sha256: {hash}            │                            │
│  │  Body: <raw binary bytes>                  │                            │
│  │ ──────────────────────────────────────────►│                            │
│  │                                            │                            │
│  │  ┌────────────────────────────────────────┐│                            │
│  │  │ S3 validates:                          ││                            │
│  │  │ • Signature not expired (< 1 hour)     ││                            │
│  │  │ • Content-Type matches signed value    ││                            │
│  │  │ • Content-Length matches signed value   ││                            │
│  │  │ • SHA-256 checksum matches header      ││                            │
│  │  │ • HTTP method is PUT (not GET/DELETE)   ││                            │
│  │  │ • Bucket path matches signed path      ││                            │
│  │  └────────────────────────────────────────┘│                            │
│  │                                            │                            │
│  │  200 OK                                    │                            │
│  │ ◄─────────────────────────────────────────│                            │
│  │                                                                         │
│  Step 4: SDK confirms upload to Langfuse Server                            │
│  ──────────────────────────────────────────────                             │
│  │                                          Langfuse Server                │
│  │  PATCH /api/public/media/{mediaId}       │                              │
│  │  Authorization: Basic base64(pk:sk)      │                              │
│  │  Body: {uploadedAt, uploadHttpStatus,    │                              │
│  │         uploadTimeMs}                    │                              │
│  │ ─────────────────────────────────────►   │                              │
│  │                                          │ Updates media record in DB   │
│  │  200 OK                                  │                              │
│  │ ◄─────────────────────────────────────   │                              │
│                                                                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### Step 1: SDK-to-Server Authentication (Basic Auth)

The SDK authenticates to the Langfuse API using HTTP Basic Authentication. From the `ApiAuthService.verifyAuthHeaderAndReturnScope()` source:

1. **Header parsing:** The `Authorization: Basic {base64}` header is decoded to extract `public_key` (username) and `secret_key` (password)
2. **Key hashing:** The secret key is hashed using SHA-256 with a server-side `SALT` environment variable: `createShaHash(secretKey, salt)`
3. **Cache lookup:** The hash is first checked in Redis (if available and `LANGFUSE_CACHE_API_KEY_ENABLED=true`). On cache miss, PostgreSQL `api_keys` table is queried by `fastHashedSecretKey`
4. **Legacy key migration:** If only the old `hashedSecretKey` (bcrypt) exists, the server verifies using `verifySecretKey()` (bcrypt compare), then backfills the fast SHA-256 hash
5. **Scope resolution:** On success, returns `{projectId, orgId, plan, accessLevel: "project", rateLimitOverrides, isIngestionSuspended}`
6. **Rate limiting:** The `POST /api/public/media` endpoint is rate-limited under the `"ingestion"` resource category

The SDK never receives or handles storage credentials. It only knows its Langfuse `public_key` and `secret_key`.

#### Step 2: Server-to-Storage Authentication (IAM / Static Keys)

The Langfuse server authenticates to the object storage backend using credentials configured via environment variables. From `getMediaStorageClient.ts`:

```typescript
s3StorageServiceClient = StorageServiceFactory.getInstance({
  bucketName,
  accessKeyId: env.LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID,
  secretAccessKey: env.LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY,
  endpoint: env.LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT,
  region: env.LANGFUSE_S3_MEDIA_UPLOAD_REGION,
  forcePathStyle: env.LANGFUSE_S3_MEDIA_UPLOAD_FORCE_PATH_STYLE === "true",
});
```

| Storage Backend | Auth Method | Source |
|----------------|-------------|--------|
| AWS S3 | Static access key/secret OR IAM role (if keys omitted, `@aws-sdk/client-s3` uses default credential chain: env vars → IMDSv2 → ECS task role) | `S3StorageService` constructor |
| Azure Blob | `StorageSharedKeyCredential(accessKeyId, secretAccessKey)` on the configured endpoint | `AzureBlobStorageService` constructor |
| Google Cloud Storage | JSON credentials string or key file path via `LANGFUSE_GOOGLE_CLOUD_STORAGE_CREDENTIALS`, or default ADC | `GoogleCloudStorageService` constructor |
| OCI Object Storage | Instance principal / workload identity / resource principal / OCI profile / session token (via `LANGFUSE_OCI_AUTH_TYPE`) | `OCIObjectStorageService.initClient()` |
| MinIO | Static access key/secret with `forcePathStyle: true` | `S3StorageService` with path-style enabled |

The `StorageServiceFactory.getInstance()` selects the backend based on flags: `LANGFUSE_USE_AZURE_BLOB`, `LANGFUSE_USE_GOOGLE_CLOUD_STORAGE`, `LANGFUSE_USE_OCI_NATIVE_OBJECT_STORAGE`, or defaults to S3-compatible.

#### Step 3: Presigned URL Generation (AWS S3 Detail)

For AWS S3, the presigned upload URL is generated using `@aws-sdk/s3-request-presigner`:

```typescript
// S3StorageService.getSignedUploadUrl()
return getSignedUrl(
  this.signedUrlClient,       // S3Client with server's credentials
  new PutObjectCommand(
    this.addSSEToParams({
      Bucket: this.bucketName,
      Key: path,                // "{prefix}{projectId}/{mediaId}.{ext}"
      ContentType: contentType,
      ChecksumSHA256: sha256Hash,
      ContentLength: contentLength,
    }),
  ),
  {
    expiresIn: ttlSeconds,       // 3600 seconds (1 hour)
    signableHeaders: new Set(["content-type", "content-length"]),
    unhoistableHeaders: new Set(["x-amz-checksum-sha256"]),
  },
);
```

**What the presigned URL constrains:**

| Constraint | Enforced By | Effect |
|-----------|-------------|--------|
| Time expiry | `expiresIn: 3600` | URL invalid after 1 hour |
| HTTP method | Signed for `PUT` only | Cannot use URL for `GET`, `DELETE`, or `HEAD` |
| Bucket + Key | `Bucket` + `Key` in signed command | Cannot write to a different path |
| Content-Type | `signableHeaders: content-type` | Must match the declared MIME type |
| Content-Length | `signableHeaders: content-length` | Must match the declared file size |
| Content integrity | `ChecksumSHA256` (unhoistable) | S3 verifies SHA-256 of uploaded bytes |
| SSE encryption | `ServerSideEncryption` + `SSEKMSKeyId` (if configured) | Forces server-side encryption on the stored object |

**The SDK never sees storage credentials.** The presigned URL contains a cryptographic signature derived from the server's AWS credentials, but the credentials themselves are not embedded or extractable.

#### Step 4: Upload Confirmation

The SDK calls `PATCH /api/public/media/{mediaId}` with the same Basic Auth credentials to report the upload outcome. The server updates the `media` table with `uploadHttpStatus`, `uploadedAt`, `uploadHttpError`, and `uploadTimeMs`.

### Download Flow: Browser UI to Object Storage

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│  DOWNLOAD AUTHENTICATION FLOW (UI rendering images/audio)                  │
│                                                                            │
│  Step 1: UI authenticates to Langfuse Server (session cookie)              │
│  ───────────────────────────────────────────────────────────                │
│                                                                            │
│  Browser (React)                          Langfuse Server                  │
│  │                                          │                              │
│  │  Trace detail view detects               │                              │
│  │  @@@langfuseMedia:type=image/png|        │                              │
│  │  id=abc123|source=base64_data_uri@@@     │                              │
│  │                                          │                              │
│  │  GET /api/public/media/{mediaId}         │                              │
│  │  Authorization: Basic base64(pk:sk)      │                              │
│  │  (or session cookie for web UI)          │                              │
│  │ ─────────────────────────────────────►   │                              │
│  │                                          │                              │
│  │                          ┌───────────────┤                              │
│  │                          │ Verify auth   │                              │
│  │                          │ Lookup media record in PostgreSQL            │
│  │                          │ Verify uploadHttpStatus == 200               │
│  │                          │ Verify media belongs to project              │
│  │                          └───────────────┤                              │
│  │                                          │                              │
│  Step 2: Server generates presigned download URL                           │
│  ──────────────────────────────────────────────                             │
│  │                          ┌───────────────┤                              │
│  │                          │ StorageService │                              │
│  │                          │ getSignedUrl(                                │
│  │                          │   media.bucketPath,                          │
│  │                          │   ttlSeconds,    // default 3600             │
│  │                          │   asAttachment: false                        │
│  │                          │ )                                            │
│  │                          │                                              │
│  │                          │ → GetObjectCommand signed with               │
│  │                          │   server's AWS credentials                   │
│  │                          │ → Read-only, time-limited                    │
│  │                          └───────────────┤                              │
│  │                                          │                              │
│  │  Response: {mediaId, url, urlExpiry,     │                              │
│  │            contentType, contentLength}   │                              │
│  │ ◄─────────────────────────────────────   │                              │
│  │                                          │                              │
│  Step 3: Browser fetches media directly from Object Storage                │
│  ──────────────────────────────────────────────────────────                 │
│  │                                          Object Storage                 │
│  │  GET {presigned_download_url}              │                            │
│  │  (no Authorization header needed)         │                            │
│  │ ──────────────────────────────────────────►│                            │
│  │                                            │                            │
│  │  ┌────────────────────────────────────────┐│                            │
│  │  │ S3 validates:                          ││                            │
│  │  │ • Signature not expired                ││                            │
│  │  │ • HTTP method is GET                   ││                            │
│  │  │ • Bucket path matches signed path      ││                            │
│  │  └────────────────────────────────────────┘│                            │
│  │                                            │                            │
│  │  200 OK                                    │                            │
│  │  Content-Type: image/png                   │                            │
│  │  Body: <binary content>                    │                            │
│  │ ◄─────────────────────────────────────────│                            │
│  │                                                                         │
│  │  <img src="{presigned_url}"> renders inline                             │
│  │  <audio src="{presigned_url}"> plays inline                             │
│                                                                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### Download Presigned URL Generation

From `media/[mediaId].ts` (GET handler):

```typescript
const media = await prisma.media.findUnique({
  where: {
    projectId_id: { projectId, id: mediaId },
  },
});

// Verify upload completed successfully
if (!media.uploadHttpStatus) throw new LangfuseNotFoundError("Media not yet uploaded");
if (!(media.uploadHttpStatus === 200 || media.uploadHttpStatus === 201))
  throw new LangfuseNotFoundError(`Media upload failed...`);

const mediaStorageClient = getMediaStorageServiceClient(media.bucketName);
const ttlSeconds = env.LANGFUSE_S3_MEDIA_DOWNLOAD_URL_EXPIRY_SECONDS;

const url = await mediaStorageClient.getSignedUrl(
  media.bucketPath,
  ttlSeconds,
  false,  // asAttachment: false → inline rendering, no download prompt
);
```

For AWS S3, the download presigned URL is generated using:

```typescript
// S3StorageService.getSignedUrl()
return getSignedUrl(
  this.signedUrlClient,
  new GetObjectCommand({
    Bucket: this.bucketName,
    Key: fileName,
    ResponseContentDisposition: asAttachment
      ? `attachment; filename="${fileName}"`
      : undefined,  // inline rendering for UI
  }),
  { expiresIn: ttlSeconds },
);
```

**Key difference from upload:** Download URLs are generated with `GetObjectCommand` (read-only). The browser can use the URL directly as an `<img>` or `<audio>` `src` attribute — no additional authentication is needed for the storage fetch.

### Programmatic Download (SDK `resolve_media_references`)

When using `resolve_media_references()` from the Python SDK to reconstruct base64 data URIs:

```text
SDK                              Langfuse Server              Object Storage
│                                  │                              │
│  GET /api/public/media/{id}      │                              │
│  Authorization: Basic pk:sk      │                              │
│ ─────────────────────────────►   │                              │
│                                  │ Verify auth                  │
│                                  │ Generate presigned GET URL   │
│  {url: "https://s3...?sig=..."}  │                              │
│ ◄─────────────────────────────   │                              │
│                                                                 │
│  GET {presigned_url}                                            │
│  (no auth header)                                               │
│ ────────────────────────────────────────────────────────────►   │
│                                                                 │
│  200 OK + binary content                                        │
│ ◄────────────────────────────────────────────────────────────   │
│                                                                 │
│  base64 encode → data:{type};base64,{content}                   │
```

### Security Properties

| Property | How Enforced |
|----------|-------------|
| **No credential leakage to SDK** | Server generates presigned URLs; SDK never receives AWS/Azure/GCS credentials |
| **No credential leakage to browser** | Download presigned URLs contain a signature, not the server's credentials. Signature is non-reversible |
| **Project isolation** | Bucket paths include `{projectId}/`; API auth verifies `projectId` before generating any URL |
| **Upload integrity** | SHA-256 checksum is signed into the upload URL and verified by S3 on receipt |
| **Time-limited access** | Upload URLs expire in 1 hour; download URLs configurable (default 1 hour) |
| **Method restriction** | Upload URLs only work for `PUT`; download URLs only work for `GET` |
| **Path restriction** | Each presigned URL is scoped to a single object path — cannot access other files |
| **Content-type enforcement** | Upload URL enforces the declared `Content-Type` via `signableHeaders` |
| **SSE encryption** | If `LANGFUSE_S3_MEDIA_UPLOAD_SSE` is configured, `ServerSideEncryption` and `SSEKMSKeyId` are included in the signed `PutObjectCommand`, enforcing encryption at rest |
| **Deduplication safety** | Existing media (same `sha256Hash` + `projectId`) returns `uploadUrl: null`, preventing re-upload. Junction table links are created via raw SQL upserts with `ON CONFLICT DO NOTHING` |
| **Rate limiting** | Media upload endpoint is rate-limited under the `"ingestion"` resource category per project/org plan |

### External Endpoint Support

The Langfuse server supports an `externalEndpoint` configuration for presigned URLs. When set, the server uses two `S3Client` instances:

- **Internal client:** Uses `LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT` for actual S3 operations (PutObject, GetObject)
- **Signed URL client:** Uses `LANGFUSE_S3_MEDIA_UPLOAD_EXTERNAL_ENDPOINT` for generating presigned URLs that the SDK/browser can reach

This supports architectures where the server communicates with S3 over a private VPC endpoint, but the SDK/browser must use a public endpoint. For Azure Blob Storage, the signed URL's internal endpoint is replaced with the external endpoint via string substitution.

### How Storage Providers Trust the SDK Upload Request

The SDK never authenticates directly to AWS S3, Azure Blob, or GCS. The presigned URL **is** the authorization — it's a self-contained, cryptographically signed token. The storage provider trusts the request because the URL could only have been produced by someone holding the server's secret key.

#### AWS S3: HMAC-SHA256 Signature Verification

When the Langfuse server calls `getSignedUrl()` from `@aws-sdk/s3-request-presigner`, it produces a URL with embedded query-string parameters. The `X-Amz-Signature` is an HMAC-SHA256 computed over the HTTP method (`PUT`), the bucket path, the signed headers (`content-type`, `content-length`), the expiry time, and the `ChecksumSHA256` value — all signed with the server's `SecretAccessKey`.

When the SDK PUTs to this URL, S3 independently recomputes the signature using its copy of the same key. If they match, the request is trusted. If any parameter is tampered with (different path, different content-type, expired), the signature mismatches and S3 returns **403 Forbidden**.

The SDK sends **no `Authorization` header** to S3. The entire authorization is in the query string parameters.

#### Azure Blob Storage: SAS Token Verification

Azure uses a Shared Access Signature (SAS) token appended to the URL, generated via `blockBlobClient.generateSasUrl()` with `BlobSASPermissions.parse("w")`. The SAS token is HMAC-signed with the storage account key, and Azure validates it server-side using the same principle.

#### Google Cloud Storage: V4 Signed URL Verification

GCS uses V4 signed URLs via `file.getSignedUrl({version: "v4", action: "write"})`. The signature is computed using the service account's private key, and GCS validates it against the corresponding public key.

#### Trust Model Summary

```text
Server has:  Secret Key (AWS) / Account Key (Azure) / SA Private Key (GCS)
                    │
                    ▼
           Signs the request parameters
           (method, path, headers, expiry)
                    │
                    ▼
           Presigned URL = endpoint + params + signature
                    │
                    ▼
           SDK receives URL, PUTs binary data
                    │
                    ▼
           Storage provider has the SAME key
           → recomputes signature from URL params
           → compares with provided signature
           → match = TRUSTED, mismatch = REJECTED
```

The SDK is trusted **not because of who it is**, but because it presents a URL that could only have been produced by someone holding the server's secret key. The SDK cannot forge, extend, or modify this permission.

### Concrete Presigned URL Example

#### What the SDK Receives from Langfuse Server

The SDK calls `POST /api/public/media` and receives a JSON response:

```json
{
  "mediaId": "n4bQgYhMfWWaL-qgxV",
  "uploadUrl": "https://langfuse-media-bucket.s3.eu-west-1.amazonaws.com/media/proj_abc123/n4bQgYhMfWWaL-qgxV.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAIOSFODNN7EXAMPLE%2F20260513%2Feu-west-1%2Fs3%2Faws4_request&X-Amz-Date=20260513T143052Z&X-Amz-Expires=3600&X-Amz-SignedHeaders=content-length%3Bcontent-type%3Bhost&X-Amz-Checksum-SHA256=n4bQgYhMfWWaL%2BqgxVrQFaO%2FTxsrC4Is0V1sFbDwCgg%3D&X-Amz-Signature=fe5f80f77d5fa3beca038a248ff027d0445342fe2855ddc963176630326f1024"
}
```

#### URL Parameter Breakdown

```text
https://langfuse-media-bucket.s3.eu-west-1.amazonaws.com
└── S3 endpoint (bucket + region)

/media/proj_abc123/n4bQgYhMfWWaL-qgxV.png
└── Object path: {prefix}{projectId}/{mediaId}.{extension}

?X-Amz-Algorithm=AWS4-HMAC-SHA256
└── Signing algorithm

&X-Amz-Credential=AKIAIOSFODNN7EXAMPLE/20260513/eu-west-1/s3/aws4_request
└── Server's access key ID (public portion only, not the secret)

&X-Amz-Date=20260513T143052Z
└── When the URL was signed

&X-Amz-Expires=3600
└── Valid for 1 hour from X-Amz-Date

&X-Amz-SignedHeaders=content-length;content-type;host
└── Headers the SDK MUST send with matching values

&X-Amz-Checksum-SHA256=n4bQgYhMfWWaL+qgxVrQFaO/TxsrC4Is0V1sFbDwCgg=
└── S3 will verify the uploaded bytes match this hash

&X-Amz-Signature=fe5f80f77d5fa3beca038a248ff027d0445342fe2855ddc963176630326f1024
└── HMAC-SHA256 signature covering ALL the above parameters
    (computed using the server's secret key)
```

#### What the SDK Sends to S3

The SDK uses the **exact same URL** as received — no modification:

```python
# From media_manager.py - _process_upload_media_job()
response = self._httpx_client.put(
    upload_url,                              # exact URL from server response
    headers={
        "Content-Type": data["content_type"],
        "x-ms-blob-type": "BlockBlob",
        "x-amz-checksum-sha256": data["content_sha256_hash"],
    },
    content=data["content_bytes"],           # raw binary
)
```

The upload URL and the URL used by the SDK to upload are the **same URL**. There is no transformation, no additional signing, and no `Authorization` header added.

## Performance Impact and Latency Analysis

Langfuse's multi-modal handling is designed to minimize impact on the application's critical path. The following analysis is derived from the source code in `media_manager.py`, `media.py`, `span.py`, and `resource_manager.py`.

### Synchronous (Critical Path) Overhead

Media detection and extraction runs **synchronously** on the application thread, inside `_process_media_and_apply_mask()`, which is called during span creation (`__init__`) and `update()`. This is the only part that adds latency to the user's LLM call path.

#### Operations on the Critical Path

| Operation | Code Location | Complexity | Estimated Latency |
|-----------|--------------|------------|-------------------|
| Recursive data traversal | `_find_and_process_media()` | $O(n)$ where $n$ = nodes in input/output tree, capped at depth 10 | Sub-millisecond for typical LLM payloads |
| Pattern matching (4 checks) | `_find_and_process_media()` | $O(1)$ per node — `isinstance()` + `dict.get()` + `str.startswith()` | Negligible |
| Base64 decode | `LangfuseMedia._parse_base64_data_uri()` | $O(m)$ where $m$ = base64 string length | ~1-5 ms for a 1 MB image; ~50-100 ms for a 10 MB file |
| SHA-256 hash | `LangfuseMedia._content_sha256_hash` (property) | $O(m)$ where $m$ = raw byte count | ~3 ms for 1 MB; ~30 ms for 10 MB (Python `hashlib`, C-backed) |
| Media ID derivation | `LangfuseMedia._get_media_id()` | $O(1)$ — base64 encode + string slice | Negligible |
| Reference string generation | `LangfuseMedia._reference_string` (property) | $O(1)$ — string formatting | Negligible |
| Queue enqueue | `queue.put(block=False)` | $O(1)$ — non-blocking put | Negligible |

#### Total Synchronous Latency Per Media Item

For a typical 500 KB image (common for vision model inputs):

$$t_{sync} = t_{traverse} + t_{base64\_decode} + t_{sha256} + t_{enqueue}$$

$$t_{sync} \approx 0.1\text{ ms} + 0.5\text{ ms} + 1.5\text{ ms} + 0.01\text{ ms} \approx 2\text{ ms}$$

For a 5 MB audio file (common for speech-to-text inputs):

$$t_{sync} \approx 0.1\text{ ms} + 5\text{ ms} + 15\text{ ms} + 0.01\text{ ms} \approx 20\text{ ms}$$

**Context:** A typical OpenAI GPT-4o vision call takes 2-10 seconds. The 2 ms synchronous overhead for a 500 KB image adds **<0.1%** to total request latency.

#### Memory Overhead on Critical Path

The synchronous path creates a second copy of the binary content:

1. **Original base64 string** in the LLM message payload (owned by the application)
2. **Decoded `content_bytes`** stored in `LangfuseMedia` object (1.33x smaller than base64)
3. **`UploadMediaJob`** holds a reference to `content_bytes` (same memory, not a copy)

Peak memory per media item:

$$M_{peak} = M_{base64} + M_{decoded} = \frac{4}{3}S + S = \frac{7}{3}S$$

where $S$ is the raw file size. For a 1 MB file, peak overhead is ~2.33 MB. The original base64 string is replaced in-place with the compact reference token (`@@@langfuseMedia:...@@@`), so the base64 memory can be reclaimed by GC once the LLM response is processed.

### Asynchronous (Background) Overhead

All network I/O for media upload runs on **background daemon threads**, completely decoupled from the application's request path.

#### Background Thread Architecture

```text
Application Thread          Background Media Thread(s)
─────────────────           ─────────────────────────
span.__init__()
  └─ _find_and_process_media()    ┌──────────────────────────────┐
       └─ queue.put(job)  ───►    │ MediaUploadConsumer.run()    │
  └─ continue execution          │   └─ process_next_upload()   │
                                  │     1. POST /media (presign) │
LLM call proceeds                │     2. PUT blob (upload)     │
unblocked                        │     3. PATCH status (confirm) │
                                  └──────────────────────────────┘
```

#### Background Upload Timing

Per media item, the background thread performs three sequential HTTP requests:

| Step | Request | Typical Latency | Notes |
|------|---------|-----------------|-------|
| 1 | `POST /api/public/media` (get presigned URL) | 20-100 ms | Server-side: DB lookup + S3 presign |
| 2 | `PUT {presigned_url}` (upload binary) | 50-5000 ms | Depends on file size and network bandwidth |
| 3 | `PATCH /api/public/media/{id}` (confirm) | 10-50 ms | Lightweight DB update |

For a 1 MB image on a 100 Mbps connection:

$$t_{background} \approx 50\text{ ms} + 80\text{ ms} + 20\text{ ms} \approx 150\text{ ms}$$

This runs entirely in the background and does **not** block the application.

#### Thread and Queue Configuration

| Parameter | Default | Source |
|-----------|---------|--------|
| Media upload threads | 1 | `LANGFUSE_MEDIA_UPLOAD_THREAD_COUNT` env var or `media_upload_thread_count` constructor arg |
| Media upload queue capacity | 100,000 items | Hardcoded in `resource_manager.py`: `Queue(100_000)` |
| Queue put behavior | Non-blocking (`block=False`) | If queue is full, media is silently dropped with warning log |
| Consumer thread type | Daemon thread | Does not block program exit (`self.daemon = True`) |
| Queue poll timeout | 1 second | `self._queue.get(block=True, timeout=1)` per item |
| Max retries per upload | 3 | Exponential backoff via `backoff.on_exception()` |

#### Queue Full Risk Assessment

With a queue capacity of 100,000 items and 1 consumer thread processing items sequentially (~150 ms each), the queue can absorb:

$$\text{Drain rate} = \frac{1}{0.15\text{s}} \approx 6.7\text{ items/second per thread}$$

At 1 thread, the queue fills in:

$$t_{fill} = \frac{100{,}000}{r_{produce} - 6.7}$$

For typical workloads producing 1-10 media items per second, the queue will never fill. For high-throughput pipelines, increasing `LANGFUSE_MEDIA_UPLOAD_THREAD_COUNT` to 2-4 provides sufficient headroom.

### Span Serialization Impact

The trace payload sent to Langfuse via OTLP is **smaller** when media is present compared to the no-media case:

| Scenario | Payload Size for 1 MB Image |
|----------|----------------------------|
| Without Langfuse media (raw base64 in span attributes) | ~1.33 MB |
| With Langfuse media (reference token replaces base64) | ~80 bytes |

The reference token `@@@langfuseMedia:type=image/png|id=abc123def456xyz789ab|source=base64_data_uri@@@` is approximately 80 bytes regardless of media size. This dramatically reduces OTLP ingestion payload, avoiding span size limits and reducing batch export latency.

### Where Media Processing is Triggered

Media detection is called from `LangfuseObservationWrapper._process_media_in_attribute()`, which is invoked at two points:

1. **Span creation** (`__init__`): processes `input`, `output`, and `metadata` — runs once when the observation is created
2. **Span update** (`.update()`): processes any updated `input`, `output`, or `metadata` — runs each time update is called with new data

Each invocation processes each field (input, output, metadata) independently through `_find_and_process_media()`.

### Toggle and Disable Behavior

When `LANGFUSE_MEDIA_UPLOAD_ENABLED=false`:

- `_find_and_process_media()` returns data unmodified (short-circuit at the first check)
- No `LangfuseMedia` objects are created
- No queue jobs are enqueued
- No background upload threads are started (consumer threads are only started if `_media_upload_enabled` is `True`)
- Raw base64 content remains in trace payloads (larger OTLP exports)
- **Overhead: effectively zero**

### Performance Summary

| Metric | Impact | Details |
|--------|--------|---------|
| Added latency per LLM call | **<5 ms** for typical images | Base64 decode + SHA-256 on critical path |
| Added latency per LLM call | **<25 ms** for large audio | 5-10 MB audio files |
| Network impact on app thread | **None** | All uploads run on background daemon threads |
| Memory overhead | **~2.3x raw file size** (transient) | Peaks during base64 decode + byte retention; released after GC |
| OTLP payload reduction | **99.99%** per media item | 1.33 MB base64 → 80 byte reference token |
| Queue capacity | **100,000 items** | Non-blocking enqueue; silent drop on overflow |
| Upload throughput | **~6.7 items/sec/thread** | Configurable thread count via env var |
| Disable overhead | **Zero** | `LANGFUSE_MEDIA_UPLOAD_ENABLED=false` short-circuits all processing |

### Telemetry Emission Timing: Before or After Upload?

A critical design question is whether spans (telemetry data) are emitted **before** or **after** the media binary is uploaded to object storage. Langfuse emits spans **before** the media upload completes. The two pipelines are fully decoupled:

```text
Time ──────────────────────────────────────────────────────────────►

Application Thread:
  ┌─────────────────┐   ┌───────────────────┐   ┌──────────────┐
  │ LLM call returns│──►│ span.__init__()    │──►│ span.end()   │
  │                 │   │  • detect media    │   │              │
  │                 │   │  • base64 decode   │   │  • OTEL span │
  │                 │   │  • SHA-256 hash    │   │    finalized  │
  │                 │   │  • enqueue upload  │   │              │
  │                 │   │  • replace w/ token│   │              │
  └─────────────────┘   └───────────────────┘   └──────┬───────┘
                                                       │
OTEL BatchSpanProcessor:                               ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ Batches spans (default: 512 spans or 5s interval)          │
  │ Exports via OTLPSpanExporter to Langfuse /otel/v1/traces   │
  │ Span payload contains reference tokens, NOT binary data    │
  └─────────────────────────────────────────────────────────────┘

Background Media Thread (concurrent, decoupled):
  ┌──────────────────────────────────────────────────────────────┐
  │ queue.get() ──► POST /media ──► PUT presigned ──► PATCH     │
  │ (may still be uploading when span has already been exported) │
  └──────────────────────────────────────────────────────────────┘
```

**Why spans are emitted before upload:**

1. **Non-blocking by design.** The `span.__init__()` method calls `_find_and_process_media()` which enqueues the `UploadMediaJob` to a background queue via `queue.put(block=False)`. It does **not** wait for the upload to finish. The span is finalized with the reference token immediately.
2. **OTEL span lifecycle is independent.** The OpenTelemetry `BatchSpanProcessor` collects ended spans and exports them in batches on its own schedule (configurable via `flush_at` and `flush_interval`). The span processor has no knowledge of or dependency on the media upload queue.
3. **Eventual consistency model.** When the Langfuse server receives a span containing `@@@langfuseMedia:type=image/png|id=abc123|source=base64_data_uri@@@`, the referenced media may not yet exist in object storage. The server links the media record to the trace via the `trace_media` / `observation_media` junction tables using the `mediaId` from the `POST /api/public/media` call, which happens asynchronously. The UI gracefully handles this by showing a placeholder or loading state until the media upload completes.

**Additional latency on telemetry (spans):**

| Component | Without Media | With Media | Delta |
|-----------|--------------|------------|-------|
| `span.__init__()` | ~0.1 ms | ~2-20 ms | +2-20 ms (base64 decode + SHA-256) |
| `span.end()` | ~0.01 ms | ~0.01 ms | **None** — no change |
| `BatchSpanProcessor` export | Depends on payload size | **Faster** (smaller payload) | **Negative** — reference tokens reduce OTLP batch size |
| Total span lifecycle | ~0.1 ms | ~2-20 ms | Net: +2-20 ms on creation only |

The key insight is that while `span.__init__()` takes slightly longer due to media detection and hashing, the `span.end()` and `BatchSpanProcessor.on_end()` export paths are **unaffected** and even **faster** because the span payload is smaller (reference tokens vs. full base64 content).

### Why S3 Presigned URLs?

Langfuse uses S3 presigned URLs (or equivalent SAS tokens for Azure Blob Storage) for both upload and download operations. This is a deliberate architectural choice with specific performance and security benefits.

#### Upload Path: SDK → Object Storage (Presigned PUT URL)

The upload flow uses a presigned PUT URL instead of uploading through the Langfuse server:

```text
Alternative A (NOT used):             Alternative B (USED):
SDK ──binary──► Langfuse Server       SDK ──metadata──► Langfuse Server
                    │                                        │
                    ▼                                        ▼
                Object Storage         SDK ──binary──► Object Storage
                                       (direct via presigned URL)
```

**Why this design was chosen:**

1. **Server never touches binary data.** The Langfuse API server only handles small JSON requests (`POST /api/public/media` is ~200 bytes). The multi-megabyte binary blob goes directly from the SDK to object storage. This prevents the server from becoming a bottleneck and avoids saturating its network bandwidth or memory with large file transfers.

2. **Horizontal scaling without proxy cost.** If the server acted as a proxy, each media upload would consume a server connection for the duration of the transfer (50 ms to 5+ seconds). With presigned URLs, the server's involvement is a single ~20 ms API call. This allows a small API server to support thousands of concurrent media uploads.

3. **No double-hop latency.** Without presigned URLs, the binary would traverse: SDK → Server → Object Storage (two hops). With presigned URLs: SDK → Object Storage (one hop). For a 5 MB file on a 100 Mbps connection, this saves ~400 ms of transfer time.

4. **Content integrity verification.** The presigned URL includes the SHA-256 hash in the upload headers (`x-amz-checksum-sha256`). S3 verifies the checksum server-side and rejects the upload if the content doesn't match. This end-to-end integrity check would require additional server-side code if the server proxied the upload.

5. **Storage provider abstraction.** The presigned URL mechanism is supported natively by AWS S3, Azure Blob Storage (via SAS tokens), Google Cloud Storage, and MinIO. The SDK doesn't need to know which storage backend is used — it just PUTs to the URL it receives.

6. **Time-limited access.** Upload presigned URLs expire after 1 hour (hardcoded: `ttlSeconds: 60 * 60`). If a URL is leaked or intercepted, it becomes useless after expiry. The URL is scoped to a specific bucket path and HTTP method, preventing misuse for reading or deleting other objects.

#### Download Path: UI/API → Object Storage (Presigned GET URL)

For rendering media in the UI or reconstructing content via `resolve_media_references()`:

1. The UI/client calls `GET /api/public/media/{mediaId}` on the Langfuse server
2. The server generates a presigned GET URL (configurable TTL, default 1 hour)
3. The UI/client fetches the binary directly from object storage using the presigned URL

**Why presigned for download too:**

- **No server egress bandwidth cost.** Media-heavy dashboards with dozens of images would overwhelm the API server if it proxied every download. Presigned URLs offload all egress to the storage provider.
- **CDN compatibility.** Presigned URLs work with CDN caching (CloudFront, Azure CDN) if the TTL and caching headers are configured appropriately.
- **Access control without authentication passthrough.** The UI can render `<img src="{presigned_url}">` without needing to pass Langfuse API credentials to the browser's image loader.

#### Presigned URL TTL Configuration

| URL Type | TTL | Configurable |
|----------|-----|-------------|
| Upload (PUT) | 1 hour | No (hardcoded server-side) |
| Download (GET) | 1 hour (default) | Yes — `LANGFUSE_S3_MEDIA_DOWNLOAD_URL_EXPIRY_SECONDS` |

## Layer 1: SDK Instrumentation Hooks

### Supported SDKs

| SDK | Package | Instrumentation Method |
|-----|---------|----------------------|
| Python | `langfuse` (PyPI) | `@observe()` decorator, OpenAI drop-in replacement (`from langfuse.openai import openai`), LangChain callback handler |
| JavaScript/TypeScript | `langfuse` (npm) | `observeOpenAI()` wrapper, Vercel AI SDK integration, manual `Langfuse` client |

### OpenAI Drop-In Replacement (Python)

The primary instrumentation path for OpenAI-based applications. The SDK wraps every OpenAI API method using `wrapt.wrap_function_wrapper`:

```python
# langfuse/openai.py - Registration
OPENAI_METHODS_V1 = [
    OpenAiDefinition(
        module="openai.resources.chat.completions",
        object="Completions",
        method="create",
        type="chat",
        sync=True,
    ),
    # ... async, embeddings, responses, etc.
]

def register_tracing():
    for resource in OPENAI_METHODS_V1:
        wrap_function_wrapper(
            resource.module,
            f"{resource.object}.{resource.method}",
            _wrap(resource) if resource.sync else _wrap_async(resource),
        )
```

### Audio Detection in OpenAI Messages

The `_process_message()` function in `openai.py` handles two audio formats:

**Input audio** (user sends audio to the model):

```python
# langfuse/openai.py - _process_message()
for content_part in content:
    if content_part.get("type") == "input_audio":
        audio_base64 = content_part.get("input_audio", {}).get("data", None)
        format = content_part.get("input_audio", {}).get("format", "wav")
        if audio_base64 is not None:
            base64_data_uri = f"data:audio/{format};base64,{audio_base64}"
            processed_content.append({
                "type": "input_audio",
                "input_audio": {
                    "data": LangfuseMedia(base64_data_uri=base64_data_uri),
                    "format": format,
                },
            })
```

**Output audio** (model returns audio response):

```python
# langfuse/openai.py - _extract_chat_response()
if kwargs.get("audio") is not None:
    audio = kwargs["audio"].__dict__
    if "data" in audio and audio["data"] is not None:
        base64_data_uri = f"data:audio/{audio.get('format', 'wav')};base64,{audio.get('data', None)}"
        audio["data"] = LangfuseMedia(base64_data_uri=base64_data_uri)
```

Images via `image_url` content parts or base64-encoded image data URIs are not handled in `_process_message()` specifically. They flow through to `MediaManager._find_and_process_media()` where generic base64 detection catches them.

## Layer 2: Media Detection and Extraction

### MediaManager._find_and_process_media()

This is the core detection engine. It recursively traverses all input/output data structures to find and extract binary content. Located in `langfuse/_task_manager/media_manager.py`.

**Control flow:**

```python
def _find_and_process_media(self, *, data, trace_id, observation_id, field):
    if not self._enabled:  # LANGFUSE_MEDIA_UPLOAD_ENABLED env var
        return data

    seen = set()       # Circular reference protection
    max_levels = 10    # Maximum recursion depth

    def _process_data_recursively(data, level):
        if id(data) in seen or level > max_levels:
            return data
        seen.add(id(data))
        # ... detection logic per provider format
```

### Detection Patterns (4 Formats)

The media manager supports four distinct detection patterns, each matching a different LLM provider's content format:

#### Pattern 1: LangfuseMedia Objects (Pre-wrapped)

If the data is already a `LangfuseMedia` instance (e.g., from `_process_message()` in the OpenAI wrapper or manually created by the user):

```python
if isinstance(data, LangfuseMedia):
    self._process_media(media=data, trace_id=trace_id, ...)
    return data
```

#### Pattern 2: Generic Base64 Data URI Strings

Matches any raw base64 data URI string regardless of provider. This catches images, audio, PDFs, and any other content encoded as data URIs:

```python
if (
    isinstance(data, str)
    and data.startswith("data:")
    and "," in data
    and data.split(",", 1)[0].endswith(";base64")
):
    media = LangfuseMedia(obj=data, base64_data_uri=data)
    self._process_media(media=media, ...)
    return media
```

**Example match:** `"data:image/png;base64,iVBORw0KGgo..."` or `"data:audio/wav;base64,UklGR..."` or `"data:application/pdf;base64,JVBERi0..."`.

#### Pattern 3: Anthropic Content Blocks

Anthropic uses `{"type": "base64", "media_type": "image/png", "data": "..."}` format:

```python
if (
    isinstance(data, dict)
    and "type" in data and data["type"] == "base64"
    and "media_type" in data
    and "data" in data
):
    media = LangfuseMedia(
        base64_data_uri=f"data:{data['media_type']};base64," + data["data"],
    )
    self._process_media(media=media, ...)
    copied = data.copy()
    copied["data"] = media  # Replace raw base64 with LangfuseMedia object
    return copied
```

#### Pattern 4: Google Vertex AI Content Blocks

Vertex AI uses `{"type": "media", "mime_type": "image/jpeg", "data": "..."}` format:

```python
if (
    isinstance(data, dict)
    and "type" in data and data["type"] == "media"
    and "mime_type" in data
    and "data" in data
):
    media = LangfuseMedia(
        base64_data_uri=f"data:{data['mime_type']};base64," + data["data"],
    )
    self._process_media(media=media, ...)
    copied = data.copy()
    copied["data"] = media
    return copied
```

### What is NOT Detected

* **Image URLs** (`https://example.com/photo.jpg`): Not extracted to S3. These are stored as-is in the trace and rendered directly from the URL in the UI.
* **File paths**: Not auto-detected. Users must explicitly create `LangfuseMedia(file_path="...", content_type="...")`.
* **Streaming audio chunks**: Not currently supported; only complete audio data after buffering.

## Layer 3: LangfuseMedia Class

Located in `langfuse/media.py`. This is the core data class that wraps media content for upload.

### Construction Modes

| Mode | Input | Use Case |
|------|-------|----------|
| `base64_data_uri` | `"data:image/png;base64,iVBOR..."` | Auto-detection from LLM payloads |
| `content_bytes` + `content_type` | Raw bytes + MIME string | Manual wrapping by user |
| `file_path` + `content_type` | File system path + MIME string | Loading from disk |

### Media ID Generation

The media ID is deterministic, derived from the content hash. This enables deduplication — the same content always produces the same media ID.

```python
def _get_media_id(self) -> Optional[str]:
    content_hash = self._content_sha256_hash  # SHA-256 of raw bytes
    if content_hash is None:
        return None
    # Convert hash to base64Url-safe format
    url_safe_content_hash = content_hash.replace("+", "-").replace("/", "_")
    # Take first 22 characters (132 bits of entropy)
    return url_safe_content_hash[:22]

@property
def _content_sha256_hash(self) -> Optional[str]:
    if self._content_bytes is None:
        return None
    sha256_hash_bytes = hashlib.sha256(self._content_bytes).digest()
    return base64.b64encode(sha256_hash_bytes).decode("utf-8")
```

**ID collision probability:** 22 base64url characters = 132 bits of entropy. Birthday paradox collision probability at 1 billion media items is approximately $10^{-21}$.

### Reference Token Format

When a `LangfuseMedia` object is serialized into the trace payload, it produces a reference token:

```text
@@@langfuseMedia:type={content_type}|id={media_id}|source={source}@@@
```

**Examples:**

* `@@@langfuseMedia:type=image/png|id=abc123def456xyz789ab|source=base64_data_uri@@@`
* `@@@langfuseMedia:type=audio/wav|id=xyz789abc123def456ab|source=bytes@@@`
* `@@@langfuseMedia:type=application/pdf|id=def456xyz789abc123ab|source=file@@@`

**Source values:** `base64_data_uri`, `bytes`, `file` — indicates how the content was originally provided.

### Supported MIME Types

Defined in `langfuse/api/MediaContentType`:

| Category | MIME Types |
|----------|-----------|
| Images | `image/png`, `image/jpeg`, `image/gif`, `image/webp`, `image/svg+xml` |
| Audio | `audio/mpeg`, `audio/mp3`, `audio/wav`, `audio/ogg`, `audio/webm` |
| Video | `video/mp4`, `video/webm`, `video/ogg` |
| Documents | `application/pdf`, `text/plain`, `text/html`, `text/csv` |
| Data | `application/json`, `application/xml` |

## Layer 4: Background Upload Queue

### Queue Architecture (Python SDK)

The upload system uses a producer-consumer pattern with a bounded in-memory queue:

```text
Main Thread                          Background Thread
    │                                      │
    │  _process_media()                    │
    │  ──► queue.put(UploadMediaJob)       │
    │       (non-blocking, block=False)    │
    │                                      │  process_next_media_upload()
    │                                      │  ──► queue.get(block=True, timeout=1)
    │                                      │  ──► _process_upload_media_job()
    │                                      │       1. POST /api/public/media
    │                                      │       2. PUT {presigned_url}
    │                                      │       3. PATCH /api/public/media/{id}
```

### UploadMediaJob Structure

```python
class UploadMediaJob(TypedDict):
    media_id: str              # SHA-256 derived, 22 chars
    content_type: str          # MIME type
    content_length: int        # Byte count
    content_bytes: bytes       # Raw binary data
    content_sha256_hash: str   # Full SHA-256 hash (base64)
    trace_id: str              # Parent trace ID
    observation_id: Optional[str]  # Parent observation ID (if any)
    field: str                 # "input", "output", or "metadata"
```

### Retry Strategy

Uses `backoff` library with exponential backoff:

* **Max retries:** 3
* **Backoff:** Exponential
* **Give up on:** 4xx errors (except 429 Too Many Requests)
* **Retry on:** 5xx errors, 429, network errors

```python
@backoff.on_exception(
    backoff.expo,
    Exception,
    max_tries=self._max_retries,
    giveup=_should_give_up,
)
def execute_task_with_backoff():
    return func(*args, **kwargs)
```

### Queue Full Behavior

If the upload queue is full, the media is silently dropped with a warning log. The trace continues with the reference token, but the referenced media will not be available.

### Environment Variable Control

| Variable | Default | Purpose |
|----------|---------|---------|
| `LANGFUSE_MEDIA_UPLOAD_ENABLED` | `True` | Master toggle; set to `false` or `0` to disable all media uploads |

## Layer 5: Server-Side Upload API

### Three-Phase Upload Protocol

**Phase 1: Request presigned upload URL**

```http
POST /api/public/media
Authorization: Basic {public_key}:{secret_key}
Content-Type: application/json

{
  "contentType": "image/png",
  "contentLength": 524288,
  "sha256Hash": "abc123...",
  "traceId": "trace-uuid",
  "observationId": "obs-uuid",   // optional
  "field": "input"               // "input" | "output" | "metadata"
}
```

**Response:**

```json
{
  "mediaId": "abc123def456xyz789ab",
  "uploadUrl": "https://s3.amazonaws.com/bucket/proj/abc123.png?X-Amz-..."
}
```

If the response has `"uploadUrl": null`, the content already exists (deduplication hit) and no upload is needed.

**Phase 2: Upload binary content**

```http
PUT {uploadUrl}
Content-Type: image/png
x-ms-blob-type: BlockBlob              # For Azure Blob Storage
x-amz-checksum-sha256: abc123...       # For AWS S3
Body: <raw binary bytes>
```

Headers are provider-specific. For GCS self-hosted setups, `x-ms-blob-type` and `x-amz-checksum-sha256` are omitted to avoid unsupported-header errors.

**Phase 3: Confirm upload status**

```http
PATCH /api/public/media/{mediaId}
{
  "uploadedAt": "2026-05-13T10:30:00.000Z",
  "uploadHttpStatus": 200,
  "uploadHttpError": "",
  "uploadTimeMs": 342
}
```

### Server-Side Deduplication Logic

The server checks for existing media before generating a presigned URL:

```typescript
// web/src/pages/api/public/media/index.ts
const existingMedia = await prisma.media.findUnique({
    where: {
        projectId_sha256Hash: {
            projectId,
            sha256Hash,    // Unique constraint: (project_id, sha_256_hash)
        },
    },
});

if (
    existingMedia &&
    existingMedia.uploadHttpStatus === 200 &&
    existingMedia.contentType === contentType
) {
    // Dedup hit: link existing media to this trace, skip upload
    return { mediaId: existingMedia.id, uploadUrl: null };
}
```

**Deduplication scope:** Per-project. The same image used in two different projects will be uploaded and stored separately.

### Media ID Consistency

Both the SDK and server independently derive the media ID from the SHA-256 hash using the same algorithm:

```typescript
// Server side (TypeScript)
function getMediaId(params: { sha256Hash: string }) {
    const urlSafeHash = sha256Hash.replaceAll("+", "-").replaceAll("/", "_");
    return urlSafeHash.slice(0, 22);  // First 22 base64Url chars
}
```

```python
# SDK side (Python)
def _get_media_id(self):
    url_safe_content_hash = content_hash.replace("+", "-").replace("/", "_")
    return url_safe_content_hash[:22]
```

The server validates that the SDK-generated `mediaId` matches its own calculation and rejects mismatches.

### Bucket Path Structure

```text
{prefix}{projectId}/{mediaId}.{extension}

Example: media/proj_abc123/def456xyz789abc123ab.png
```

File extension is derived from the MIME content type server-side.

### Upload Status Reporting Mechanism

The `uploadHttpStatus` field in the `media` table is **not** set by the server polling storage. The **SDK client** reports the upload outcome back to the Langfuse server via the Phase 3 PATCH call. There is no periodic storage check or reconciliation.

#### Success Path

After a successful PUT to the presigned URL, the SDK's background thread calls back:

```python
# From media_manager.py - _process_upload_media_job()
upload_start_time = time.time()
upload_response = self._request_with_backoff(_upload_with_status_check)
upload_time_ms = int((time.time() - upload_start_time) * 1000)

# Report success to Langfuse server
self._request_with_backoff(
    self._api_client.media.patch,
    media_id=data["media_id"],
    uploaded_at=_get_timestamp(),
    upload_http_status=upload_response.status_code,  # 200
    upload_http_error=upload_response.text,
    upload_time_ms=upload_time_ms,
)
```

#### Failure Path

If the PUT to storage fails, the SDK still reports the failure:

```python
except httpx.HTTPStatusError as e:
    upload_time_ms = int((time.time() - upload_start_time) * 1000)
    failed_response = e.response

    if failed_response is not None:
        self._request_with_backoff(
            self._api_client.media.patch,
            media_id=data["media_id"],
            uploaded_at=_get_timestamp(),
            upload_http_status=failed_response.status_code,  # e.g. 403, 500
            upload_http_error=failed_response.text,
            upload_time_ms=upload_time_ms,
        )
```

#### Server-Side PATCH Handler

From `media/[mediaId].ts`, the server updates PostgreSQL and records metrics:

```typescript
await prisma.media.update({
  where: { projectId_id: { projectId, id: mediaId } },
  data: {
    uploadedAt,
    uploadHttpStatus,
    uploadHttpError: uploadHttpStatus === 200 ? null : uploadHttpError,
  },
});

recordIncrement("langfuse.media.upload_http_status", 1, {
  status_code: uploadHttpStatus,
});

if (uploadTimeMs) {
  recordHistogram("langfuse.media.upload_time_ms", uploadTimeMs, {
    status_code: uploadHttpStatus,
  });
}
```

#### SDK Crash or Network Failure Scenario

If the SDK crashes after uploading to S3 but before sending the PATCH call, `uploadHttpStatus` remains `NULL` in PostgreSQL. The download handler checks for this:

```typescript
if (!media.uploadHttpStatus)
  throw new LangfuseNotFoundError("Media not yet uploaded");
```

The media reference token remains in the trace, but the UI shows a "not yet uploaded" state. There is **no server-side reconciliation or storage polling** — the record stays in this state permanently. This is a deliberate trade-off for the fully client-driven, fire-and-forget design.

| Scenario | `uploadHttpStatus` | UI Behavior |
|----------|-------------------|-------------|
| Upload succeeded, PATCH succeeded | `200` | Media renders inline |
| Upload failed (e.g. 403), PATCH succeeded | `403` | Error message shown |
| Upload succeeded, SDK crashed before PATCH | `NULL` | "Not yet uploaded" placeholder |
| Upload never attempted (queue full) | `NULL` | "Not yet uploaded" placeholder |

## Layer 6: Database Schema

### PostgreSQL Tables

**`media` table** — Core media metadata:

| Column | Type | Description |
|--------|------|-------------|
| `id` | string (PK) | Media ID (22-char SHA-256 derivative) |
| `project_id` | string (FK) | Owning project |
| `sha_256_hash` | string | Full SHA-256 hash of content |
| `bucket_path` | string | S3/Blob path within bucket |
| `bucket_name` | string | S3/Blob bucket name |
| `content_type` | string | MIME type |
| `content_length` | int | Size in bytes |
| `upload_http_status` | int | HTTP status from upload PUT (200 = success) |
| `upload_http_error` | string | Error text if upload failed |
| `uploaded_at` | timestamp | When upload completed |

**Unique constraint:** `(project_id, sha_256_hash)` — enforces per-project deduplication.

**`trace_media` table** — Links media to traces:

| Column | Type | Description |
|--------|------|-------------|
| `id` | string (PK) | Junction ID |
| `project_id` | string | Project context |
| `trace_id` | string (FK) | Trace the media belongs to |
| `media_id` | string (FK) | Referenced media |
| `field` | string | `"input"`, `"output"`, or `"metadata"` |

**`observation_media` table** — Links media to observations (spans):

| Column | Type | Description |
|--------|------|-------------|
| `id` | string (PK) | Junction ID |
| `project_id` | string | Project context |
| `trace_id` | string | Trace context |
| `observation_id` | string (FK) | Observation the media belongs to |
| `media_id` | string (FK) | Referenced media |
| `field` | string | `"input"`, `"output"`, or `"metadata"` |

### ClickHouse

Trace and observation data (including the reference token strings) is stored in ClickHouse for fast analytical queries. The reference tokens are embedded in JSON text columns as part of the input/output payloads.

## Layer 7: Object Storage Configuration

### Supported Backends

| Backend | Configuration | Notes |
|---------|--------------|-------|
| AWS S3 | Standard S3 env vars | Default for Langfuse Cloud |
| Azure Blob Storage | S3-compatible API via endpoint config | Uses `x-ms-blob-type: BlockBlob` header |
| Google Cloud Storage | S3-compatible API | Self-hosted GCS omits AWS/Azure-specific headers |
| MinIO | Set `FORCE_PATH_STYLE=true` | For self-hosted / air-gapped deployments |

### Server Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LANGFUSE_S3_MEDIA_UPLOAD_BUCKET` | Yes | Bucket name for media files |
| `LANGFUSE_S3_MEDIA_UPLOAD_PREFIX` | No | Path prefix within bucket (must end with `/`) |
| `LANGFUSE_S3_MEDIA_UPLOAD_REGION` | No | Bucket region |
| `LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT` | No | Custom endpoint URL (for non-AWS) |
| `LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID` | No | Access key (omit for IAM roles) |
| `LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY` | No | Secret key |
| `LANGFUSE_S3_MEDIA_UPLOAD_FORCE_PATH_STYLE` | No | Required for MinIO |
| `LANGFUSE_S3_MEDIA_MAX_CONTENT_LENGTH` | No | Max upload size; default 1 GB |
| `LANGFUSE_S3_MEDIA_DOWNLOAD_URL_EXPIRY_SECONDS` | No | Presigned download URL TTL; default 3600s (1 hour) |
| `LANGFUSE_S3_CONCURRENT_WRITES` | No | Max concurrent S3 writes; default 50 |
| `LANGFUSE_S3_CONCURRENT_READS` | No | Max concurrent S3 reads; default 50 |

### Presigned URL Configuration

* **Upload URL TTL:** 1 hour (hardcoded server-side: `ttlSeconds: 60 * 60`)
* **Download URL TTL:** Configurable via `LANGFUSE_S3_MEDIA_DOWNLOAD_URL_EXPIRY_SECONDS`, default 1 hour
* **Max file size:** Configurable via `LANGFUSE_S3_MEDIA_MAX_CONTENT_LENGTH`, default 1 GB

## Layer 8: UI Rendering

### What is Stored in the Span/Trace Record

The span **does not** contain a presigned URL or a storage URL. It contains a **reference token** — a plain text marker embedded in the span's `input`, `output`, or `metadata` JSON stored in ClickHouse:

```json
{
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "What's in this image?"},
        {"type": "image_url", "image_url": {
          "data": "@@@langfuseMedia:type=image/png|id=n4bQgYhMfWWaL-qgxV|source=base64_data_uri@@@"
        }}
      ]
    }
  ]
}
```

The reference token has no URL, no signature, no expiry. It is a permanent, stable identifier that can be resolved to a download URL on demand.

### Reference Token Detection

The Langfuse web UI scans all trace/observation input, output, and metadata fields for the reference token pattern:

```text
@@@langfuseMedia:type={MIME_TYPE}|id={MEDIA_ID}|source={SOURCE}@@@
```

When detected, the UI initiates the download flow described below.

### UI Media Download Flow

The UI uses a **different presigned URL** from the one used for upload. The upload presigned URL (PUT) has long expired by the time someone views the trace. The server generates a **new** presigned GET URL on demand.

```text
Step 1: UI detects reference token in span data
─────────────────────────────────────────────────
  UI parses: @@@langfuseMedia:type=image/png|id=n4bQgYhMfWWaL-qgxV|source=base64_data_uri@@@
  Extracts:  mediaId = "n4bQgYhMfWWaL-qgxV"
             contentType = "image/png"

Step 2: UI calls Langfuse API for a fresh download URL
──────────────────────────────────────────────────────
  GET /api/public/media/n4bQgYhMfWWaL-qgxV
  Authorization: Basic base64(pk:sk)

Step 3: Server generates a NEW presigned GET URL
─────────────────────────────────────────────────
  Server:
  1. Looks up media record in PostgreSQL by (projectId, mediaId)
  2. Verifies uploadHttpStatus == 200 (upload completed successfully)
  3. Reads bucketPath: "media/proj_abc123/n4bQgYhMfWWaL-qgxV.png"
  4. Calls storageService.getSignedUrl(bucketPath, ttlSeconds=3600)
     → Signs a GetObjectCommand with server's credentials
     → Produces a brand new presigned URL for reading

  Response:
  {
    "mediaId": "n4bQgYhMfWWaL-qgxV",
    "contentType": "image/png",
    "contentLength": 524288,
    "url": "https://langfuse-media-bucket.s3.eu-west-1.amazonaws.com/media/proj_abc123/n4bQgYhMfWWaL-qgxV.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA...&X-Amz-Date=20260513T180000Z&X-Amz-Expires=3600&X-Amz-Signature=def456...",
    "urlExpiry": "2026-05-13T19:00:00.000Z",
    "uploadedAt": "2026-05-13T14:31:00.000Z"
  }

Step 4: Browser fetches binary directly from S3
────────────────────────────────────────────────
  <img src="{presigned_download_url}" /> → GET to S3 → PNG binary → renders inline
  <audio src="{presigned_download_url}" /> → GET to S3 → audio binary → plays inline

  No Authorization header needed — the presigned URL IS the auth.
```

### Upload URL vs. Download URL Comparison

| Aspect | Upload URL (SDK received) | Download URL (UI receives) |
|--------|--------------------------|---------------------------|
| Generated when | SDK calls `POST /api/public/media` | UI calls `GET /api/public/media/{mediaId}` |
| HTTP method signed | `PUT` | `GET` |
| S3 command | `PutObjectCommand` | `GetObjectCommand` |
| Contains checksum constraint | Yes (`ChecksumSHA256`) | No |
| Contains content-length constraint | Yes | No |
| TTL | 1 hour (hardcoded) | 1 hour (configurable) |
| Reusable | No — one-time use for that upload | Yes — can be used multiple times until expiry |
| Interchangeable | No — a PUT URL cannot be used for GET, and vice versa |

### Presigned URL Expiry During Viewing

If a user keeps a trace detail page open for over 1 hour, the presigned download URLs expire. The browser gets a 403 from S3 when attempting to load the media. The UI must call `GET /api/public/media/{mediaId}` again to get a fresh URL with a new expiry. Each call generates a brand new presigned URL.

### Rendering by Content Type

| Content Type | UI Rendering |
|-------------|-------------|
| `image/*` | Inline `<img>` tag with presigned download URL as `src` |
| `audio/*` | HTML5 `<audio>` player with presigned download URL and playback controls |
| `application/pdf` | Embedded PDF viewer or download link |
| `text/*` | Formatted text display |
| External URLs (`https://...`) | Rendered directly from source URL without S3 round-trip |

### External URL Rendering

For image URLs not extracted to S3 (standard `https://` URLs in content parts), Langfuse renders them inline using:

* Markdown image syntax: `![alt](https://example.com/image.jpg)`
* OpenAI content part format: `{"type": "image_url", "image_url": {"url": "..."}}`

These are rendered directly from the source URL without passing through Langfuse's object storage.

## Layer 9: Content Reconstruction

### resolve_media_references()

This utility method enables reconstructing original payloads from reference tokens — critical for fine-tuning datasets, replay, and evaluation workflows.

```python
from langfuse import get_client

langfuse = get_client()

# Object containing reference tokens
obj = {
    "image": "@@@langfuseMedia:type=image/jpeg|id=abc123|source=bytes@@@",
    "nested": {
        "pdf": "@@@langfuseMedia:type=application/pdf|id=def456|source=bytes@@@"
    }
}

# Resolve all references back to base64 data URIs
resolved = LangfuseMedia.resolve_media_references(
    obj=obj,
    langfuse_client=langfuse,
    resolve_with="base64_data_uri",
    max_depth=10,
    content_fetch_timeout_seconds=10,
)

# Result:
# {
#     "image": "data:image/jpeg;base64,/9j/4AAQSkZJRg...",
#     "nested": {
#         "pdf": "data:application/pdf;base64,JVBERi0xLjcK..."
#     }
# }
```

### Resolution Flow

1. Recursively traverse the object (dict, list, string, nested objects)
2. Use regex `r"@@@langfuseMedia:.+?@@@"` to find all reference tokens in strings
3. Parse each token to extract `media_id` and `content_type`
4. Call `langfuse_client.api.media.get(media_id)` → returns presigned download URL
5. Fetch binary content from presigned URL via `httpx.get()`
6. Base64-encode the binary and construct data URI: `data:{content_type};base64,{base64_content}`
7. Replace the reference token with the data URI in the string
8. On failure: log warning, leave reference token unchanged

### Failure Handling

* Individual reference resolution failures do not fail the entire operation
* Failed references are left as reference tokens (not replaced)
* `content_fetch_timeout_seconds` prevents long hangs (default 10s per fetch)
* Uses the SDK's existing `httpx.Client` for connection pooling

## Data Flow Summary

```text
Step  Component                Action                                  Data Format
─────────────────────────────────────────────────────────────────────────────────────
1     User App                 Call OpenAI with image                  base64 data URI in messages
2     SDK openai.py            _process_message() wraps audio          LangfuseMedia object
3     SDK media_manager.py     _find_and_process_media() detects       LangfuseMedia per pattern
4     SDK media.py             SHA-256 hash → media_id                 22-char deterministic ID
5     SDK media.py             Generate reference string               @@@langfuseMedia:...@@@
6     SDK media_manager.py     Enqueue UploadMediaJob                  Queue<UploadMediaJob>
7     SDK (background)         POST /api/public/media                  Get presigned URL or dedup
8     SDK (background)         PUT presigned URL                       Binary upload to S3/Blob
9     SDK (background)         PATCH /api/public/media/{id}            Confirm upload status
10    SDK client               Send trace with reference tokens        JSON with @@@...@@@ strings
11    Server                   Store in ClickHouse + PostgreSQL        Metadata + junction tables
12    UI                       Detect @@@langfuseMedia:...@@@ tokens   Regex scan
13    UI                       GET /api/public/media/{id}              Presigned download URL
14    UI                       Render inline                           <img>, <audio>, etc.
15    Reconstruction           resolve_media_references()              base64 data URI restored
```

## Key Design Takeaways for Azure Monitor

| Langfuse Decision | Rationale | Azure Monitor Consideration |
|-------------------|-----------|---------------------------|
| Client-side extraction | Prevents large payloads from hitting ingestion API; reduces bandwidth | Azure Monitor Distro could intercept in `configure_azure_monitor()` pipeline |
| SHA-256 content-addressable IDs | Deterministic, enables dedup without server round-trip for ID generation | Same approach works with Log Analytics; server validates consistency |
| Background thread upload | Non-blocking; does not add latency to LLM call path | Azure Monitor Distro already uses background batching for spans |
| Per-project dedup scope | Balances isolation with storage efficiency | Per-workspace dedup aligns with Log Analytics workspace model |
| Presigned URLs for upload and download | SDK uploads directly to storage; server never touches binary data | Azure Blob SAS tokens provide equivalent functionality |
| PostgreSQL for metadata, S3 for binary | Separation of concerns; independent scaling and retention | Log Analytics for metadata, Azure Blob for binary mirrors this |
| Reference token format | Self-contained; UI can parse without server call to identify MIME type | Azure Monitor could use similar format: `@@azmon-media:...@@` |
| 1 GB max file size | Generous limit covers all practical LLM media payloads | Azure Monitor could default lower (e.g., 25 MB) given OTLP pipeline constraints |
| No PII scanning | Gap in the market | Azure AI Content Safety integration is a differentiator |
