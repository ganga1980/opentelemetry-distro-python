# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from microsoft.opentelemetry._distro import use_microsoft_opentelemetry
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent

endpoint = "<AZURE_OPENAI_ENDPOINT>"
model_name = "gpt-4.1"
deployment_name = "gpt-4.1"

api_key = "<AZURE_OPENAI_API_KEY>"

use_microsoft_opentelemetry(
    enable_azure_monitor=True,
    sampling_ratio=1.0,
    instrumentation_options={
        "langchain": {
            "enabled": True,
            # Optional: set static agent identity on all LangChain spans.
            # These appear as gen_ai.agent.* attributes in your telemetry.
            # If omitted, the distro infers agent_name from LangChain's
            # runtime metadata when available.
            "agent_id": "travel-assistant-001",
            "agent_name": "Travel_Assistant",
        },
    },
)


def main():

    # ChatOpenAI
    llm = ChatOpenAI(
        model="gpt-4.1",
        temperature=0.1,
        max_tokens=100,
        top_p=0.9,
        frequency_penalty=0.5,
        presence_penalty=0.5,
        stop_sequences=["\n", "Human:", "AI:"],
        seed=100,
        api_key=api_key,  # Do not include api_key and base_url if using the OPENAI_API_KEY environment variable
        base_url=endpoint,
    )

    messages = [
        SystemMessage(content="You are a helpful assistant!"),
        HumanMessage(content="What is the capital of France?"),
    ]

    result = llm.invoke(messages)
    print("LLM output:\n", result)

    @tool
    def get_population(city: str) -> str:
        """Get the population of a city."""
        populations = {
            "paris": "2.1 million",
            "london": "8.9 million",
            "tokyo": "14 million",
            "new york": "8.3 million",
        }
        return populations.get(city.lower(), f"Population data not available for {city}.")

    @tool
    def get_famous_landmark(city: str) -> str:
        """Get the most famous landmark in a city."""
        landmarks = {
            "paris": "Eiffel Tower",
            "london": "Big Ben",
            "tokyo": "Tokyo Tower",
            "new york": "Statue of Liberty",
        }
        return landmarks.get(city.lower(), f"No landmark info for {city}.")

    agent = create_agent(
        llm,
        tools=[get_population, get_famous_landmark],
        system_prompt="You are a helpful travel assistant. Use the tools to answer questions about cities.",
        name="Travel_Assistant",
    )

    print("\n--- Agent run ---")
    user_input = "What is the population of Paris and what is its most famous landmark?"
    agent_result = agent.invoke({"messages": [("human", user_input)]})
    output = agent_result["messages"][-1].content
    print("Agent output:\n", output)


if __name__ == "__main__":
    main()
