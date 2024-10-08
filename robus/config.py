import os
import sys
import logging
import importlib.metadata
import pkgutil
import chromadb
from chromadb import Settings
from base64 import b64encode
from bs4 import BeautifulSoup
from typing import TypeVar, Generic, Union

from langchain_chroma import Chroma
from langchain_community.embeddings import DashScopeEmbeddings
from pydantic import BaseModel
from typing import Optional
from langchain_community.llms.cloudflare_workersai import CloudflareWorkersAI
from langchain_community.llms.tongyi import Tongyi
from langchain_openai import ChatOpenAI
from pathlib import Path
import json
import yaml
from dotenv import load_dotenv
from supabase.client import Client, create_client
import markdown
import requests
import shutil
from langchain.chains.conversation.memory import ConversationBufferMemory, ConversationSummaryMemory, \
    ConversationBufferWindowMemory, ConversationSummaryBufferMemory
from langchain.chains.conversation.base import ConversationChain
from secrets import token_bytes
from langchain.callbacks import AsyncIteratorCallbackHandler
from langchain_community.vectorstores import SupabaseVectorStore
from langchain_community.embeddings.cloudflare_workersai import (
    CloudflareWorkersAIEmbeddings,
)
from dotenv import load_dotenv, find_dotenv
from langchain_community.chat_message_histories import RedisChatMessageHistory

# from constants import ERROR_MESSAGES

####################################
# Load .env file
####################################

BACKEND_DIR = Path(__file__).parent  # the path containing this file
BASE_DIR = BACKEND_DIR.parent  # the path containing the backend/
# print(f"BASE_DIR: ", BASE_DIR)  # /Users/pangmengting/Documents/workspace/python-learning

STATIC_DIR = Path(os.getenv("STATIC_DIR", BACKEND_DIR / "public")).resolve()
# print(f"STATIC_DIR:", STATIC_DIR)
UPLOAD_DIR = Path(BASE_DIR / "file").resolve()
# print(f"UPLOAD_DIR==>{UPLOAD_DIR}")
# UPLOAD_DIR==>/Users/pangmengting/Documents/workspace/python-learning/file

try:
    load_dotenv(find_dotenv(str(BASE_DIR / ".env")))
except ImportError:
    print("dotenv not installed, skipping...")

####################################
# LOGGING
####################################

log_levels = ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]

GLOBAL_LOG_LEVEL = os.environ.get("GLOBAL_LOG_LEVEL", "INFO").upper()
if GLOBAL_LOG_LEVEL in log_levels:
    logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL, force=True)
else:
    GLOBAL_LOG_LEVEL = "INFO"

log = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data")).resolve()
# /Users/pangmengting/Documents/workspace/python-learning/robus/data
# print(f"==>>DATA_DIR:{DATA_DIR}")

CONFIG_DATA = {}


def save_config():
    try:
        with open(f"{DATA_DIR}/config.json", "w") as f:
            json.dump(CONFIG_DATA, f, indent="\t")
    except Exception as e:
        log.exception(e)


T = TypeVar("T")


class PersistentConfig(Generic[T]):
    def __init__(self, env_name: str, config_path: str, env_value: T):
        self.env_name = env_name
        self.config_path = config_path
        self.env_value = env_value
        self.value = env_value

    def __str__(self):
        return str(self.value)

    @property
    def __dict__(self):
        raise TypeError(
            "PersistentConfig object cannot be converted to dict, use config_get or .value instead."
        )

    def __getattribute__(self, item):
        if item == "__dict__":
            raise TypeError(
                "PersistentConfig object cannot be converted to dict, use config_get or .value instead."
            )
        return super().__getattribute__(item)

    def save(self):
        # Don't save if the value is the same as the env value and the config value
        if self.env_value == self.value:
            if self.config_value == self.value:
                return
        log.info(f"Saving '{self.env_name}' to config.json")
        path_parts = self.config_path.split(".")
        config = CONFIG_DATA
        for key in path_parts[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]
        config[path_parts[-1]] = self.value
        save_config()
        self.config_value = self.value


class AppConfig:
    _state: dict[str, PersistentConfig]

    def __init__(self):
        super().__setattr__("_state", {})

    def __setattr__(self, key, value):
        if isinstance(value, PersistentConfig):
            self._state[key] = value
        else:
            self._state[key].value = value
            self._state[key].save()

    def __getattr__(self, key):
        return self._state[key].value


PersistentConfigTest = PersistentConfig(
    "test-key",
    "test.key",
    "这是测试value"
)

CHUNK_SIZE = PersistentConfig(
    "CHUNK_SIZE", "rag.chunk_size", int(os.environ.get("CHUNK_SIZE", "1500"))
)
CHUNK_OVERLAP = PersistentConfig(
    "CHUNK_OVERLAP",
    "rag.chunk_overlap",
    int(os.environ.get("CHUNK_OVERLAP", "100")),
)

# print(os.environ.get("SUPABASE_TOKEN", "123"))

supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")
# supabase_token = os.environ.get("SUPABASE_TOKEN")

# print(f"supabase_url: " + supabase_url)
# print(f"supabase_key: " + supabase_key)
# print(f"supabase_token: " + supabase_token)

SUPABASE: Client = create_client(supabase_url, supabase_key)

# @cf/baai/bge-large-en-v1.5
# 维度是：1024
# @cf/baai/bge-small-en-v1.5
# 维度是：384
embeddings = CloudflareWorkersAIEmbeddings(
    account_id=os.getenv('CF_ACCOUNT_ID'),
    api_token=os.getenv('CF_API_TOKEN'),
    model_name="@cf/baai/bge-small-en-v1.5",
)
# dimension：1536
qw_embeddings = DashScopeEmbeddings(
    model="text-embedding-v2", dashscope_api_key=os.getenv('DASHSCOPE_API_KEY')
)

vectorstore = SupabaseVectorStore(
    embedding=embeddings,
    client=SUPABASE,
    table_name="bge_small_vector",
    query_name="bge_small_match_documents",
)

# Can be "similarity" (default), "mmr", or "similarity_score_threshold".
subabase_retriever = vectorstore.as_retriever(search_type="similarity")
# subabase_retriever.search_kwargs['distance_metric'] = 'cos'
# subabase_retriever.search_kwargs['fetch_k'] = 20
# subabase_retriever.search_kwargs['maximal_marginal_relevance'] = True
subabase_retriever.search_kwargs['k'] = 3

# callback = AsyncIteratorCallbackHandler()

ms_llm = ChatOpenAI(
    openai_api_base=os.getenv('OPENAI_API_BASE'),
    openai_api_key=os.getenv('OPENAI_API_KEY'),
    model_name="moonshot-v1-8k",
    temperature=0,
    streaming=True,
)

cf_llm = CloudflareWorkersAI(
    account_id=os.getenv('CF_ACCOUNT_ID'),
    api_token=os.getenv('CF_API_TOKEN'),
    model='@cf/meta/llama-3-8b-instruct',
    streaming=True,
)

qw_llm = Tongyi(
    model='qwen2-1.5b-instruct',
    streaming=True,
)

# qwen兼容openai接口
qw_llm_openai = ChatOpenAI(
    openai_api_base=os.getenv('DASHSCOPE_API_BASE'),
    openai_api_key=os.getenv('DASHSCOPE_API_KEY'),
    model_name="qwen2-1.5b-instruct",
    temperature=0,
    streaming=True
)

# groq兼容openai接口
groq_llm_openai = ChatOpenAI(
    openai_api_base=os.getenv('GROQ_API_BASE'),
    openai_api_key=os.getenv('GROQ_API_KEY'),
    model_name="llama3-70b-8192",
    temperature=0,
    streaming=True,
)

zp_llm_openai = ChatOpenAI(
    openai_api_base='https://open.bigmodel.cn/api/paas/v4/',
    openai_api_key=os.getenv('ZHIPUAI_API_KEY'),
    model_name="glm-4",
    temperature=0,
    streaming=True,
)

conversationChain = ConversationChain(llm=qw_llm_openai, memory=ConversationBufferWindowMemory(k=2))

# /Users/pangmengting/Documents/workspace/python-learning/data
CHROMA_DATA_PATH = f"{DATA_DIR}/chroma_vector_db"
CHROMA_CLIENT = chromadb.PersistentClient(
    path=CHROMA_DATA_PATH,
    settings=Settings(allow_reset=True, anonymized_telemetry=False),
)

fix_collection_name = 'yxk-know-index-2'
vectordb = Chroma(collection_name=fix_collection_name,
                  # persist_directory=CHROMA_DATA_PATH,
                  client=CHROMA_CLIENT,  # 共享同一个客户端实例
                  embedding_function=qw_embeddings)
chroma_retriever = vectordb.as_retriever()

# REDIS_URL = "redis://192.168.22.238:6379/0"
# session_id = "robus"
# redis_chat_history = RedisChatMessageHistory(session_id, url=REDIS_URL)


# def get_message_history(session_id: str) -> RedisChatMessageHistory:
#     return RedisChatMessageHistory(session_id, url=REDIS_URL)
