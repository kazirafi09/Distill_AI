from uuid import uuid4

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.workflows.jd.graph import build_jd_graph


def main() -> None:
    graph = build_jd_graph(InMemorySaver())

    config = {
        "configurable": {
            "thread_id": str(uuid4()),
        },
        "recursion_limit": 50,
    }

    result = graph.invoke(
        {
            "messages": [
                HumanMessage(content="I want a job description."),
            ],
        },
        config,
    )

    while result.get("__interrupt__"):
        payload = result["__interrupt__"][0].value

        if payload.get("type") == "requirements":
            print(f"\nChatbot: {payload['question']}")
            message = input("You (or type cancel): ").strip()

            if not message:
                print("Please enter a reply.")
                continue

            if message.casefold() in {"cancel", "cancle"}:
                decision = {"action": "cancel"}
            else:
                decision = {
                    "action": "reply",
                    "message": message,
                }

        else:
            saved_state = graph.get_state(config).values

            print("\n--- Consolidated Requirements ---")
            print(saved_state.get("requirements", ""))

            print("\n--- Draft JD ---")

            print(payload["draft_jd"])

            allowed_actions = payload["allowed_actions"]
            print(f"\nAvailable actions: {', '.join(allowed_actions)}")

            action = input("Action: ").strip().lower()

            if action not in allowed_actions:
                print("Choose an available action.")
                continue

            decision = {"action": action}

            if action == "revise":
                feedback = input("What should change? ").strip()

                if not feedback:
                    print("Please enter revision feedback.")
                    continue

                decision["feedback"] = feedback

        result = graph.invoke(
            Command(resume=decision),
            config,
        )

    print(f"\nFinal status: {result.get('status')}")

    if result.get("status") == "approved":
        print("\n--- Approved JD ---")
        print(result["approved_jd"])

    elif result.get("status") == "failed":
        print(f"Error code: {result.get('error_code')}")


if __name__ == "__main__":
    main()