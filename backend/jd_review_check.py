from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.workflows.jd.graph import build_jd_graph

graph = build_jd_graph(InMemorySaver())
config = {"configurable": {"thread_id": str(uuid4())}}

result = graph.invoke({
    "requirements": (
        "Role: Junior Python Developer. "
        "Responsibilities: build REST APIs and maintain database queries. "
        "Required skills: Python, FastAPI, PostgreSQL and Git. "
        "Preferred skill: Docker. Experience: entry-level."
    ),
    "revision_count": 0,
    "status": "drafting",
}, config)

while "__interrupt__" in result:
    review = result["__interrupt__"][0].value

    print(review["draft_jd"])
    print("Revision count:", review["revision_count"])
    print("Allowed actions:", ", ".join(review["allowed_actions"]))

    action = input("Your action: ").strip().lower()

    if action not in review["allowed_actions"]:
        print("Invalid action. Try again.")
        continue

    decision = {"action": action}

    if action == "revise":
        feedback = input("Requested changes: ").strip()
        if not feedback:
            print("Feedback cannot be empty.")
            continue
        decision["feedback"] = feedback

    result = graph.invoke(
        Command(resume=decision),
        config,
    )

print("Final status:", result["status"])
print("Error:", result.get("error_code"))