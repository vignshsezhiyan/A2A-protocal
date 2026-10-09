"""Discover the agent from its Agent Card and send it a message over A2A.

    python client.py "what is 18% of 2450?"
    python client.py "what time is it in Asia/Kolkata?" --url https://<your-app>.onrender.com

Env: A2A_AGENT_URL (default http://127.0.0.1:10010), A2A_API_KEY (if the agent requires one).
"""

import argparse
import asyncio
import os

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types.a2a_pb2 import Role, SendMessageRequest, TaskState
from dotenv import load_dotenv

load_dotenv()


async def main(text: str, url: str) -> None:
    key = os.environ.get("A2A_API_KEY", "")
    headers = {"X-API-Key": key} if key else {}
    async with httpx.AsyncClient(headers=headers, timeout=90) as http:
        # 1. Discover: fetch the public Agent Card.
        card = await A2ACardResolver(httpx_client=http, base_url=url).get_agent_card()
        print(f"Discovered: {card.name} -> {card.supported_interfaces[0].url}")
        print("Skills    :", ", ".join(s.id for s in card.skills))

        # 2. Build a client from the card and send a message (API key travels on the httpx client).
        client = await create_client(agent=card, client_config=ClientConfig(streaming=False,
                                                                            httpx_client=http))
        try:
            request = SendMessageRequest(message=new_text_message(text, role=Role.ROLE_USER))
            async for event in client.send_message(request):
                if event.WhichOneof("payload") == "task":
                    task = event.task
                    print(f"State     : {TaskState.Name(task.status.state)}")
                    for artifact in task.artifacts:
                        for part in artifact.parts:
                            print(f"Answer    : {part.text}")
                    if task.status.message.parts:
                        print(f"Status    : {task.status.message.parts[0].text}")
        finally:
            await client.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("text")
    p.add_argument("--url", default=os.environ.get("A2A_AGENT_URL", "http://127.0.0.1:10010"))
    args = p.parse_args()
    asyncio.run(main(args.text, args.url))
