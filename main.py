import contextlib
import uuid

import chainlit as cl
from langchain.prompts import ChatPromptTemplate
from langchain_core.messages import AIMessageChunk, AIMessage, HumanMessage
from langchain_ollama import ChatOllama
from langchain_postgres import PostgresChatMessageHistory, chat_message_histories
from psycopg import AsyncConnection

# Create the table schema (only needs to be done once)
table_name = "chat_history"
jdbc_conn_string = "postgresql://rw_user:rw_user_pwd@localhost:5432/db_for_mcp"


@contextlib.asynccontextmanager
async def pg_connection():
    conn = await AsyncConnection.connect(jdbc_conn_string)
    try:
        yield conn
    finally:
        await conn.close()


async def create_chat_table():
    async with pg_connection() as conn:
        table_exists = False
        async with conn.cursor() as cur:
            await cur.execute(f"""
                            SELECT EXISTS (
                                SELECT 1
                                FROM information_schema.tables
                                WHERE table_name = '{table_name}'
                            );
                        """)
            result = await cur.fetchone()
            table_exists = result[0]
        if table_exists:
            return
        else:
            await chat_message_histories.PostgresChatMessageHistory.acreate_tables(conn, table_name)


chain = ChatPromptTemplate.from_messages(
    [
        ("human", "{text}"),
    ]
) | ChatOllama(
    model="deepseek-r1:1.5b",
    base_url="http://localhost:11434", # docker exec 
    extract_reasoning=True
)


@cl.header_auth_callback
def header_auth_callback(headers: dict) -> cl.User | None:
    user = headers.get("x-user")
    email = headers.get("x-email")
    if email and user:
        # your authorization here
        return cl.User(identifier=email, metadata={"role": "admin", "provider": "header"}, display_name=user)
    else:
        # fail authentication
        return None


@cl.on_chat_start
async def on_chat_start():
    await create_chat_table()
    await cl.Message(
        content=f"Welcome {cl.context.session.user.display_name} {cl.context.session.user.identifier} to the chat!",
        id=uuid.uuid4().hex).send()


@cl.on_message
async def on_message(message: cl.Message):
    h = None
    # Run the LLM chain with the user input
    response = cl.Message(content="", id=uuid.uuid4().hex)
    async for token in chain.astream({'text': message.content, 'chat_ctx': cl.chat_context.to_openai()}):
        if isinstance(token, str):
            await response.stream_token(token)
        elif isinstance(token, AIMessageChunk):
            await response.stream_token(token.content)
        elif isinstance(token, AIMessage):
            await response.stream_token(token.content)
        else:
            await response.stream_token(str(token))

    async with pg_connection() as conn:
        h = PostgresChatMessageHistory(
            table_name,
            cl.context.session.id,
            async_connection=conn,
        )
        await h.aadd_messages([HumanMessage(content=message.content), AIMessage(content=response.content)])
    await response.send()
