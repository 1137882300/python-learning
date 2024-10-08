import json
from fastapi.responses import FileResponse
from fastapi import FastAPI
# from config import SUPABASE
from pathlib import Path
import json

from langchain.retrievers import EnsembleRetriever, ContextualCompressionRetriever
from langchain.retrievers.document_compressors import EmbeddingsFilter, DocumentCompressorPipeline, LLMChainExtractor
from langchain.schema import messages_to_dict
import logging
from langchain_community.chat_message_histories import FileChatMessageHistory
from langchain_community.document_compressors import JinaRerank
from langchain_community.document_transformers import EmbeddingsRedundantFilter
from langchain_community.retrievers import BM25Retriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_text_splitters import CharacterTextSplitter

from robus.config import SUPABASE, ms_llm, cf_llm, qw_llm, qw_llm_openai, groq_llm_openai, conversationChain, \
    chroma_retriever, embeddings, CHROMA_CLIENT, qw_embeddings, fix_collection_name, zp_llm_openai
from fastapi.responses import Response, StreamingResponse, JSONResponse
from langchain.chains.conversation.memory import ConversationBufferMemory, ConversationSummaryMemory, \
    ConversationBufferWindowMemory, ConversationSummaryBufferMemory
from langchain.chains.conversation.base import ConversationChain
from typing import AsyncIterable, Awaitable, Iterator, List
import asyncio
from typing import Dict, Any, Iterator
from starlette.schemas import OpenAPIResponse
from langchain_core.messages import BaseMessageChunk, BaseMessage
from langchain_core.output_parsers import StrOutputParser, BaseOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.prompts import ChatPromptTemplate
from langchain.callbacks import AsyncIteratorCallbackHandler
from langchain.schema.runnable import RunnablePassthrough, RunnableConfig
from langchain.load.serializable import Serializable
from typing import Optional, Dict
from langchain_core.runnables.utils import Input
from langchain.schema.runnable import Runnable
# from langchain import PromptTemplate
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableParallel
from operator import itemgetter
from langchain_community.document_loaders import UnstructuredURLLoader
from langchain_community.document_loaders import WebBaseLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import SupabaseVectorStore
from langchain.chains.summarize import load_summarize_chain
from langchain.retrievers.multi_query import MultiQueryRetriever, LineListOutputParser
import logging
from langchain_core.runnables.history import RunnableWithMessageHistory
import datetime

# >>>>>>>>>>基础>>>>>>>>>>>>>>
log = logging.getLogger(__name__)
log.setLevel("INFO")

logging.basicConfig()
logging.getLogger("langchain.retrievers.multi_query").setLevel(logging.INFO)

app = FastAPI()


# >>>>>>>>>>接口>>>>>>>>>>>>>>>
# 返回页面
@app.get("/")
async def chroma_homepage():
    file_path = Path(__file__).parent / "index.html"
    return FileResponse(file_path)


# 返回页面2
@app.get("/upload")
async def chroma_upload():
    file_path = Path(__file__).parent / "upload.html"
    return FileResponse(file_path)


@app.post("/ask")
def ask(body: dict):
    return Response(call_llm(body['question']))


@app.post("/advance/retriever/ask")
def ask(body: dict):
    question = body['question']
    user_id = 666
    splitter = CharacterTextSplitter(chunk_size=300, chunk_overlap=0, separator=". ")
    redundant_filter = EmbeddingsRedundantFilter(embeddings=qw_embeddings)
    relevant_filter = EmbeddingsFilter(embeddings=qw_embeddings, similarity_threshold=0.1)
    # DocumentCompressorPipeline： 使用 Transformer 管道的文档压缩器。
    pipeline_compressor = DocumentCompressorPipeline(
        transformers=[splitter, redundant_filter, relevant_filter]
    )
    compression_retriever = ContextualCompressionRetriever(base_compressor=pipeline_compressor,
                                                           base_retriever=chroma_retriever)
    retrieve_docs = (lambda x: x["input"]) | compression_retriever

    chain = common(user_id, retrieve_docs)

    def generate():
        for chunk in chain.stream({"input": question}):
            if "answer" in chunk:
                yield f"{chunk['answer']}"

    return StreamingResponse(generate(), media_type="text/event-stream")


def common(user_id: str, retrieve_docs):
    system_prompt = get_prompt_en()
    prompts = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("human", "{input}"),
        ]
    )
    today = datetime.date.today()
    current_date = today.strftime("%Y%m%d")
    path = f'/Users/pangmengting/Documents/workspace/python-learning/data/history/conversation_{current_date}'
    memory = get_memory(path, user_id)
    rag_chain_from_docs = (
            RunnablePassthrough.assign(
                context=(lambda x: format_docs(x["retriever_context"])),
                history=memory.load_memory_variables
            )
            | prompts
            | qw_llm_openai
            | StrOutputParser()
    )

    chain = (
        RunnablePassthrough.assign(retriever_context=retrieve_docs)
        .assign(answer=rag_chain_from_docs)
        .assign(
            memory_update=lambda x: memory.save_context(
                {"input": x["input"]},
                {"output": x["answer"]}
            )
        )
    )
    return chain


class CusLineListOutputParser(BaseOutputParser[List[str]]):

    def parse(self, text: str) -> List[str]:
        lines = text.strip().split("\n")
        lines = [line for line in lines if line.strip()]
        return lines


@app.post("/ensemble/bm25/chroma/retriever/ask")
def ask(body: dict):
    question = body['question']
    user_id = 888
    collection_name = fix_collection_name

    system_prompt = get_prompt_cn()

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("human", "{input}"),
        ]
    )

    collection = CHROMA_CLIENT.get_collection(name=collection_name)
    documents = collection.get()

    bm25_retriever = BM25Retriever.from_texts(
        texts=documents.get("documents"),
        metadatas=documents.get("metadatas"),
    )

    # compressor = LLMChainExtractor.from_llm(qw_llm_openai)
    # compression_retriever = ContextualCompressionRetriever(base_compressor=compressor, base_retriever=chroma_retriever)

    # multi_query_Retriever = MultiQueryRetriever.from_llm(retriever=chroma_retriever, llm=qw_llm_openai)
    # output_parser = CusLineListOutputParser()
    #
    # QUERY_PROMPT = PromptTemplate(
    #     input_variables=["question"],
    #     template="""您是一个旅游类型的销售人工智能语言模型助手。您的任务是针对给定的用户问题生成3个不同版本，以便从向量数据库中检索相关文档。
    #     通过从多个角度生成用户问题，您的目标是帮助用户克服基于距离的相似性搜索的一些局限性。
    #     提供这些替代问题，用换行符分隔。原始问题：{question}""",
    # )
    #
    # # Chain
    # multi_query_chain = QUERY_PROMPT | qw_llm_openai | output_parser
    #
    # multi_query_Retriever = MultiQueryRetriever(
    #     retriever=chroma_retriever, llm_chain=multi_query_chain, parser_key="lines"
    # )

    ensemble_retriever = EnsembleRetriever(
        retrievers=[bm25_retriever, chroma_retriever], weights=[0.9, 0.5]
    )

    # compressor = JinaRerank()

    # compression_retriever = ContextualCompressionRetriever(
    #     base_compressor=compressor, base_retriever=ensemble_retriever
    # )

    retrieve_docs = (lambda x: x["input"]) | ensemble_retriever

    today = datetime.date.today()
    current_date = today.strftime("%Y%m%d")
    path = f'/Users/pangmengting/Documents/workspace/python-learning/data/history/conversation_{current_date}'
    memory = get_memory(path, user_id)
    rag_chain_from_docs = (
            RunnablePassthrough.assign(
                context=(lambda x: format_docs(x["retriever_context"])),
                history=memory.load_memory_variables
            )
            | prompt
            | zp_llm_openai
            | StrOutputParser()
    )

    chain = (
        RunnablePassthrough.assign(retriever_context=retrieve_docs)
        .assign(answer=rag_chain_from_docs)
        .assign(
            memory_update=lambda x: memory.save_context(
                {"input": x["input"]},
                {"output": x["answer"]}
            )
        )
    )

    def generate():
        for chunk in chain.stream({"input": question}):
            if "answer" in chunk:
                yield f"{chunk['answer']}"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/v2/stream/rag/memory/user/ask")
def ask(body: dict):
    question = body['question']
    user_id = 888

    system_prompt = get_prompt_en()

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("human", "{input}"),
        ]
    )

    today = datetime.date.today()
    current_date = today.strftime("%Y%m%d")
    path = f'/Users/pangmengting/Documents/workspace/python-learning/data/history/conversation_{current_date}'

    memory = get_memory(path, user_id)
    rag_chain_from_docs = (
            RunnablePassthrough.assign(
                context=(lambda x: format_docs(x["retriever_context"])),
                history=memory.load_memory_variables
            )
            | prompt
            | qw_llm_openai
            | StrOutputParser()
    )
    retrieve_docs = (lambda x: x["input"]) | chroma_retriever

    chain = (
        RunnablePassthrough.assign(retriever_context=retrieve_docs)
        .assign(answer=rag_chain_from_docs)
        .assign(
            memory_update=lambda x: memory.save_context(
                {"input": x["input"]},
                {"output": x["answer"]}
            )
        )
    )

    # return chain.invoke({"input": question})["answer"]
    # 流式返回，经返回["answer"]的值
    def generate():
        # Iterator[Output]:
        for chunk in chain.stream({"input": question}):
            if "answer" in chunk:
                # 生成包含 answer 和 retriever_context 中每个文档的 page_content 和 metadata 的字符串
                yield f"{chunk['answer']}"
            # if "retriever_context" in chunk:
            #     yield f"source：\n\n"
            #     for index, doc in enumerate(chunk["retriever_context"]):
            #         yield f"\n{doc.metadata['source']}\n"

    # if "answer" in chunk:
    #     yield f"{chunk['answer']}"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/v2/stream/rag/memory/ask")
def ask(body: dict):
    question = body['question']

    system_prompt = (
        "You are an assistant for question-answering tasks. "
        "Use the following pieces of retrieved context to answer "
        "the question. If you don't know the answer, say that you "
        "don't know. Use three sentences maximum and keep the "
        "answer concise."
        "\n\n"
        "{context}"
        "\n\n"
        "Previous conversation:\n{history}"
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("human", "{input}"),
        ]
    )
    # print(path) /Users/pangmengting/Documents/workspace/python-learning/robus/api
    # memory1
    # memory = ConversationBufferMemory(return_messages=True)

    # memory2
    path = '/Users/pangmengting/Documents/workspace/python-learning/data/history/conversation_20240709-robus.json'
    message_history = CustomFileChatMessageHistory(file_path=path)
    # memory = ConversationBufferMemory(chat_memory=message_history, return_messages=True)

    # memory3
    memory = ConversationBufferWindowMemory(k=2, chat_memory=message_history, return_messages=True)

    rag_chain_from_docs = (
            RunnablePassthrough.assign(
                context=(lambda x: format_docs(x["retriever_context"])),
                history=memory.load_memory_variables
            )
            | prompt
            | qw_llm_openai
            | StrOutputParser()
    )
    retrieve_docs = (lambda x: x["input"]) | chroma_retriever

    chain = (
        RunnablePassthrough.assign(retriever_context=retrieve_docs)
        .assign(answer=rag_chain_from_docs)
        .assign(
            memory_update=lambda x: memory.save_context(
                {"input": x["input"]},
                {"output": x["answer"]}
            )
        )
    )

    # return chain.invoke({"input": question})["answer"]
    # 流式返回，经返回["answer"]的值
    def generate():
        # Iterator[Output]:
        for chunk in chain.stream({"input": question}):
            if "answer" in chunk:
                yield f"{chunk['answer']}"

    return StreamingResponse(generate(), media_type="text/event-stream")


# stream + rag + memory ❌ 暂时没有memory
@app.post("/stream/rag/memory/ask")
def ask(body: dict):
    question = body['question']

    chat_prompt = ChatPromptTemplate.from_template(prompt())
    chat_history = conversationChain.memory.buffer
    print(chat_history)

    # 预处理输入的数据
    def pre_input(prompt: str) -> Dict:
        # rag
        context = str(chroma_retriever | format_docs)
        return {
            "context": context,
            "question": prompt,
            "chat_history": chat_history
        }

    chain = (
            RunnableLambda(pre_input) |
            {
                "context": itemgetter('context'),
                "question": itemgetter('question'),
                "chat_history": itemgetter('chat_history'),
            }
            | chat_prompt
            | RunnablePassthrough()
            | StdOutputRunnable()
            | qw_llm_openai
            | StrOutputParser()
    )

    # 流式返回
    def generate():
        # Iterator[Output]:
        for chunk in chain.stream(question):
            for key in chunk:
                yield key

    return StreamingResponse(generate(), media_type="text/event-stream")


# 基于: rag + memory 的问答
@app.post("/rag/memory/ask")
def ask(body: dict):
    question = body['question']

    chat_prompt = ChatPromptTemplate.from_template(prompt())
    chat_history = conversationChain.memory.buffer

    # 预处理输入的数据
    def pre_input(prompt: str) -> Dict:
        context = str(chroma_retriever | format_docs)
        return {
            "context": context,
            "question": prompt,
            "chat_history": chat_history
        }

    rag_chain = (
            RunnableLambda(pre_input) |
            {
                "context": itemgetter('context'),
                "question": itemgetter('question'),
                "chat_history": itemgetter('chat_history')
            }
            | chat_prompt
            | RunnablePassthrough()
            | StdOutputRunnable()
            | qw_llm_openai
            | StrOutputParser()
    )

    # print(rag_chain)

    result = rag_chain.invoke(question)

    return result


# stream + rag
@app.post("/stream/rag/multi/retriever/ask")
def ask(body: dict):
    question = body['question']

    # 预处理输入的数据
    def pre_input(prompt: str) -> Dict:
        retriever_from_llm = MultiQueryRetriever.from_llm(retriever=chroma_retriever, llm=qw_llm_openai)
        docs = retriever_from_llm.invoke(question)
        context = str(format_docs(docs))
        return {
            "context": context,
            "question": prompt,
        }

    rag_chain = (
            RunnableLambda(pre_input) |
            {
                "context": itemgetter('context'),
                "question": itemgetter('question'),
            }
            | ChatPromptTemplate.from_template(stream_rag_prompt())
            | qw_llm_openai
            | StrOutputParser()
    )

    def generate():
        # Iterator[Output]:
        for chunk in rag_chain.stream(question):
            for key in chunk:
                yield key

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/stream/rag/ask")
def ask(body: dict):
    question = body['question']
    # collection_name = fix_collection_name

    rag_chain = (
            {
                "context": (chroma_retriever | format_docs),
                "question": (RunnablePassthrough() | StdOutputRunnable())
            }
            | ChatPromptTemplate.from_template(stream_rag_prompt())
            | qw_llm_openai
            | StrOutputParser()
    )

    def generate():
        # Iterator[Output]:
        for chunk in rag_chain.stream(question):
            for key in chunk:
                yield key

    return StreamingResponse(generate(), media_type="text/event-stream")


# 基于知识库的问答
@app.post("/rag/ask")
def ask(body: dict):
    question = body['question']

    rag_chain = (
            {"context": (chroma_retriever | format_docs), "question": RunnablePassthrough()}
            | ChatPromptTemplate.from_template(prompt())
            | qw_llm_openai
            | StrOutputParser()
    )

    result = rag_chain.invoke(question)

    return result


# 暂时不是stream输出方式 ❌
# stream式 + memory，输出
@app.post("/stream/memory/ask")
def ask(body: dict):
    question = body['question']

    def generate():
        for chunk in conversationChain.stream(question):
            for key in chunk:
                print(key)
                yield key
                # input
                # history
                # response

    return StreamingResponse(generate(), media_type="text/event-stream")


# 存在记忆 的输出
@app.post("/memory/ask")
def ask(body: dict):
    result = conversationChain.invoke(body['question'])
    # {'input': '请问你是谁啊', 'history': 'Human: 你是谁啊\nAI: 我是阿里云开发的一种人工智能模型，我叫通义千问。', 'response':
    # '我是阿里云开发的一种人工智能模型，我叫通义千问。'}
    print(result)
    return JSONResponse(result)


# stream式输出
@app.post("/stream/ask")
def ask(body: dict):
    # Iterator[BaseMessageChunk]:
    result = qw_llm_openai.stream(body['question'])

    # Streaming 返回
    return StreamingResponse(
        (str(chunk.content) for chunk in result),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"}
    )


@app.get("/chat")
async def generate_chat_completion():
    r = call_llm("chat")
    response = StreamingResponse(
        r.content,
        status_code=r.status,
        headers=dict(r.headers),
    )
    content_type = response.headers.get("Content-Type")
    citations = []
    if "text/event-stream" in content_type:
        return StreamingResponse(
            openai_stream_wrapper(response.body_iterator, citations),
        )
    if "application/x-ndjson" in content_type:
        return StreamingResponse(
            ollama_stream_wrapper(response.body_iterator, citations),
        )


@app.get("/select")
def select():
    response = SUPABASE.table('documents').select("*").execute()
    return response


# >>>>>>>>>>类>>>>>>>>>>>>>>>
# 创建了一个 CustomFileChatMessageHistory 类，继承自 FileChatMessageHistory。
# 重写了 add_message 方法，在使用 json.dumps() 时添加了 ensure_ascii=False 参数，并指定了 encoding='utf-8'。这确保了中文字符被正确地编码。
# 同样修改了 clear 方法，以保持一致性。
# 使用这个自定义的 CustomFileChatMessageHistory 类来创建 message_history。
class CustomFileChatMessageHistory(FileChatMessageHistory):
    def add_message(self, message: BaseMessage) -> None:
        """Append the message to the record in the local file"""
        messages = messages_to_dict(self.messages)
        messages.append(messages_to_dict([message])[0])
        # 确保 ensure_ascii=False，才能正确展示中文
        self.file_path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding='utf-8')

    def clear(self) -> None:
        """Clear all messages from the record"""
        self.file_path.write_text(json.dumps([], ensure_ascii=False, indent=2), encoding='utf-8')


class CustomWithUserFileChatMessageHistory(FileChatMessageHistory):
    def __init__(self, file_path: str, user_id: str):
        # super().__init__(file_path)
        self.file_path = Path(f"{file_path}_{user_id}.json")
        self.user_id = user_id
        if not self.file_path.exists():
            self.file_path.write_text(json.dumps([]))

    def add_message(self, message):
        messages = self.messages
        messages.append(messages_to_dict([message])[0])
        self.file_path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding='utf-8')

    def clear(self):
        self.file_path.write_text(json.dumps([]), encoding='utf-8')

    @property
    def messages(self):
        return json.loads(self.file_path.read_text(encoding='utf-8'))


def get_memory(file_path: str, user_id: str):
    message_history = CustomWithUserFileChatMessageHistory(file_path, user_id)
    return ConversationBufferWindowMemory(k=2, chat_memory=message_history, return_messages=True)


class ChromaRetriever(BaseRetriever):
    collection: Any
    embedding_function: Any
    top_n: int

    def _get_relevant_documents(
            self,
            query: str,
            *,
            run_manager: CallbackManagerForRetrieverRun,
    ) -> List[Document]:
        query_embeddings = self.embedding_function(query)

        results = self.collection.query(
            query_embeddings=[query_embeddings],
            n_results=self.top_n,
        )

        ids = results["ids"][0]
        metadatas = results["metadatas"][0]
        documents = results["documents"][0]

        results = []
        for idx in range(len(ids)):
            results.append(
                Document(
                    metadata=metadatas[idx],
                    page_content=documents[idx],
                )
            )
        return results


# 自定义一个继承Runnable的类
class StdOutputRunnable(Serializable, Runnable[Input, Input]):
    @property
    def lc_serializable(self) -> bool:
        return True

    def invoke(self, input: Dict, config: Optional[RunnableConfig] = None) -> Input:
        # print(f"Hey, I received the name {input['name']}")
        print(input)
        return self._call_with_config(lambda x: x, input, config)


class StdOutputRunnableContext(Serializable, Runnable[Input, Input]):
    @property
    def lc_serializable(self) -> bool:
        return True

    def invoke(self, input: Dict, config: Optional[RunnableConfig] = None) -> Input:
        print(f"Context ==> {input['context']}")
        return self._call_with_config(lambda x: x, input, config)


def get_history(docs):
    return {"chat_history": 'b64_images'}


# >>>>>>>>>>方法>>>>>>>>>>>>>>>
def call_llm(question: str):
    return cf_llm.invoke(question)


# def format_docs(docs):
#     return "\n\n".join(doc.page_content for doc in docs)


# def query_doc(
#         collection_name: str,
#         query: str,
#         embedding_function,
#         k: int,
# ):
#     try:
#         collection = CHROMA_CLIENT.get_collection(name=collection_name)
#         query_embeddings = embedding_function(query)
#
#         result = collection.query(
#             query_embeddings=[query_embeddings],
#             n_results=k,
#         )
#
#         log.info(f"query_doc:result {result}")
#         return result
#     except Exception as e:
#         raise e


def format_docs(docs):
    context = "\n".join(doc.page_content for doc in docs)
    print(f"\n{'-' * 100}\n".join([f"==>Document {i + 1}:\n" + d.page_content for i, d in enumerate(docs)]))
    return context


def pretty_print_docs(docs):
    print(f"\n{'-' * 100}\n".join([f"==>Document {i + 1}:\n\n" + d.page_content for i, d in enumerate(docs)]))


def get_simple_prompt():
    return """
           You are an assistant for question-answering tasks.  
           Use the following pieces of retrieved context to answer  
           the question. If you don't know the answer, say that you  
           don't know. Use three sentences maximum and keep the  
           answer concise. 
           \n\n 
           {context} 
           \n\n"
           Previous conversation:\n{history} 
        """


def get_prompt_en():
    return """
# Role
You are secrets.toml seasoned travel product expert at YouXiaKe (游侠客) enterprise. You are enthusiastic and professional, capable of tailoring high-quality travel product recommendations based on customers' specific needs. You communicate naturally and fluently with clients, maintaining secrets.toml humanized conversation style throughout without revealing yourself as an AI model. You are highly sensitive to controversial or sensitive issues, fully protect customers' rights, and provide detailed, rich, and clear answers, never being vague or perfunctory.

## Skills
### Skill 1: Personalized Travel Product Recommendation
Proactively gather key information from customers, including but not limited to:
Specific travel dates and time arrangements
Desired destination (cities or specific attractions)
Budget range
Number of travelers and group composition (e.g., family, couples, friends)
Special needs or preferences (e.g., cuisine, outdoor activities, cultural experiences)
Travel style (e.g., relaxed leisure, adventurous, cultural immersion)
The complete title of the product needs to be extracted as secrets.toml recommendation.
Based on the information provided by the customer, recommend the most suitable travel product. Use the following response format:
=====
🏝 Travel Product Name: <Product Name>
📅 Departure Date: <Specific departure date>
🌆 Destination: <Detailed destination information>
👥 Suitable for: <Recommended traveler group>
🕰 Duration: <Length of the trip>
💰 Price: <Clear product price, including what's covered>
💡 Product Highlights: <Concise 100-character summary of product features>
🎫 Booking Method: <Clear booking channels and process>
=====
### Skill 2: Professional Query Resolution
Accurately answer customer questions based on the provided context information and chat history. When faced with uncertain information, honestly acknowledge it and provide possible solutions or further consultation channels.

Context information: {context}
Chat history: {history}

### Skill 3: Travel Advice and Tips
Provide practical advice related to the chosen destination, such as the best travel seasons, essential items to pack, local cultural taboos, recommended local cuisines, etc.

## Limitations:
Strictly focus on travel-related topics, do not respond to inquiries unrelated to travel.
Follow the specified format to organize output content, maintaining consistency and clarity.
Strictly limit the product highlights description to 100 characters, emphasizing core selling points.
Conduct all communication in Chinese, with secrets.toml warm and natural language style that is engaging.
Always consider customer safety and comfort when providing advice, not recommending potentially risky activities.
Respect customer privacy, do not request unnecessary personal information.
Interaction Process:
Greet warmly to establish secrets.toml cordial atmosphere.
Thoroughly understand customer needs, collecting key information.
Recommend the most suitable travel product based on the collected information.
Patiently answer customer questions and provide additional travel advice.
Guide customers through the booking process or provide channels for further consultation.
Summarize the conversation, ensure customer satisfaction, and invite subsequent feedback.
"""


def get_prompt_cn():
    return """ 
# 角色
你是游侠客企业经验丰富、热情洋溢且专业的旅游产品专家，熟知游侠客所有旅游产品的详细资讯。能以亲切、自然且人性化的方式与客户无障碍交流，始终把保障客户权益放在首位，给出清晰、丰富且毫无歧义的回答。

## 技能
### 技能 1: 专业问题解答
依据给定的上下文信息和聊天记录，精确且详尽地回应客户关于旅游产品的疑问。着重留意日期、地点、价格、产品名称等关键要素。若信息不清晰，需诚恳告知，并提供可能的解决办法或进一步咨询的途径。回复示例：
=====
    - 对于您询问的[具体问题]，目前的状况是[具体回答]。倘若信息不准确，您能够通过[解决办法或咨询途径]获取更确切的信息。
=====

### 技能 2: 个性化旅游产品推荐
积极主动地收集客户的关键信息，涵盖但不限于：
- 明确的出行日期及详细的时间安排
- 确切的目标目的地（精确至城市或特定景点）
- 清晰的预算范围
- 出行人数及构成（如家庭、情侣、朋友等）
- 特殊需求或偏好（比如美食、户外活动、文化体验等）
- 特定的旅行风格（像轻松休闲、冒险刺激、文化深度等）
根据上述获取到的信息及上下文，提取关键要点，如产品名称、详细目的地信息、行程天数、价格等。为客户推荐最为适配的旅游产品。回复格式如下：
=====
🏝 旅游产品名: <产品名称>
🌆 目的地: <详细目的地信息>
🕰 行程天数: <旅程持续时间，几天几夜>
💰 价格: <产品价格，包含成人价格和儿童价格>
=====

### 技能 3: 旅行建议与 tips
为客户提供其所选目的地的实用指南，包含最佳旅游季节、必备物品、当地文化禁忌、特色美食推荐等方面。回复示例：
=====
    - 有关[目的地]，最佳旅游季节是[具体季节]，您需要准备[必备物品]，同时要留意当地的文化禁忌[列举禁忌]，特色美食有[美食推荐]。
=====

### 技能 4: 使用以下在 <context></context> XML 标签内的上下文作为您学到的知识。
<context>
    {context}\n
    {history}
</context>
鉴于上下文信息，回答查询。
 
## 限制:
若依据现有信息无法回答客户问题，直接回复“不知道”。
只回应与旅游相关的客户咨询，避免无关及过度的回答，不重复作答。
严格围绕旅游主题进行交流，不涉及无关话题。
按照规定格式组织输出内容，保证一致性与清晰度。
产品亮点描述控制在 50 字以内，突出核心卖点。
全程使用中文与客户交流，语言亲切自然且富有感染力。
提供建议时，着重考虑客户的安全与舒适度，不推荐存在潜在风险的活动。
尊重客户隐私，不索要非必要的个人信息。
互动流程:
热情友好地问候客户，营造轻松愉悦的交流氛围。
细致全面地了解客户需求，精准收集关键信息。
依据收集到的信息，为客户推荐适宜的旅游产品。
耐心解答客户的疑问，给予实用的旅行建议。
引导客户进行预订，或提供进一步咨询的渠道。
总结对话内容，确保客户满意，邀请客户进行后续反馈。
"""


def get_prompt():
    return """# Character As secrets.toml seasoned travel consultant at 游侠客, I specialize in answering customer questions about 
    all aspects of travel products. My knowledge base encompasses the details of various travel products, 
    including their features and benefits. When responding to customer inquiries, I prioritize recommending products 
    before providing detailed information. I will utilize the retrieved information to answer your questions to the 
    best of my ability. If I am unable to provide secrets.toml satisfactory answer, I will inform you accordingly. I will strive 
    to keep my responses concise and informative, adhering to secrets.toml maximum of three sentences. Please feel free to ask 
    me any travel-related questions you may have.
                
        ## Skills
        ### Skill 1: Collect Travel Requirements
        - Destination: Ask the user for their travel destination.
        - Travel Dates: Clarify the user's planned travel dates.
        - Group Size and Age Range: Understand the number of travelers and their age range.
        - Budget: Obtain secrets.toml rough budget.
        - Special Requirements: Inquire about any specific travel needs or preferences, such as accessibility or vegetarian options.
        
        ### Skill 2: Provide Personalized Recommendations
        Based on the collected information, provide suitable group and product recommendations when requested by the user. Format example:
        =====
           - 🎉 Travel Group Name: <Travel Group Name>
           - 👨‍👩‍👧‍👦 Suitable for: <Explain the target audience for this travel group, e.g., families, couples>
           - 💲 Price: <Adult and child prices>
           - 🌟 Main Activities: <List key activities>
           - 🌤 Climate Overview: <Briefly describe the climate conditions of the destination>
           - 👍 Acceptance Rate: <Provide secrets.toml specific acceptance rate percentage>
        =====
        
        ### Skill 3: Answer Travel Questions Accurately answer travel-related questions based on the provided 
        context. If the background information is insufficient, honestly inform the user that you cannot provide the 
        related information directly. Answer user questions based on the given travel-related context. If the context 
        does not cover the answer, honestly inform the user that you don't know, ensuring the accuracy of the 
        response. Context: {context} Previous conversation: {history}
                
        ## Constraints: - Provide detailed and specific answers, avoiding vagueness or overly simplistic 
        generalizations. - Handle sensitive topics or potentially controversial questions with care and respect for 
        user rights. - Always comply with company guidelines, ensuring all information is truthful and meets company 
        standards. - There's no need to point out that you're an AI model, as it's clear to me. Just answer my 
        questions and communicate with me naturally, without constantly reminding me that you're an AI model."""


def stream_rag_prompt():
    return """
            # 角色
            您是游侠客旅游公司的专业客服，致力于为用户提供高品质的旅游咨询与推荐服务。
            
            ## 技能
            ### 技能 1: 收集用户旅游需求信息
            1. 当用户咨询时，引导用户提供以下关键信息：
                - 目的地：耐心询问用户想去的旅游地点。
                - 旅行时间：明确用户计划出发的具体日期。
                - 人数和年龄：了解旅行团的规模及成员年龄情况，尤其关注是否有儿童。
                - 预算范围：获取用户大致的预算额度。
                - 特殊需求：询问用户是否有诸如无障碍设施、素食选项等特殊需求或偏好。
            2. 若用户想了解特定城市的旅游信息，进一步收集以下内容：
                - 城市名称：确定用户感兴趣的城市。
                - 旅游类型：明晰用户对自然风光、文化体验、冒险活动等旅游类型的倾向。
            
            ### 技能 2: 提供个性化旅游推荐
            1. 根据用户提供的完整需求信息，为其推荐合适的旅行团和旅游产品。
            2. 推荐时，按照以下格式回复：
            =====
               -  🎉 旅行团名称: <旅行团名称>
               -  👨‍👩‍👧‍👦 适用人群: <说明适合的对象，如家庭、情侣等>
               -  💲 价格: <成人价格和儿童价格>
               -  🌟 特色活动: <列举主要活动>
               -  🌤 气候概况: <简要描述当地气候>
               -  👍 好评率: <给出具体百分比>
            =====
        
            ### 技能 3: 回答旅游问题
            1. 依据给定的旅游相关上下文来回答用户的问题。若上下文未涵盖答案，诚实告知不知，确保回答的精准性。
            问题: {question}
            上下文: {context}
            
            ## 限制:
            - 仅围绕旅游相关内容进行交流和推荐，拒绝回答无关话题。
            - 输出内容必须严格按照给定格式组织，不得随意更改。
            - 回复的信息应准确、详细且具有针对性。
            - 所有问题均用中文回答。
            """


def stream_rag_prompt2():
    return '''
    # Character
    You're secrets.toml knowledgeable assistant capable of providing concise answers to secrets.toml variety of questions, drawing from the context provided, and admitting when you don't know the answer.
    
    ## Skills
    1. **Answering Questions:** Utilize the given context to answer user questions. If the answer is not clear from the context, truthfully state that the answer is unknown to maintain accuracy in your responses.
    Question: {question}
    Context: {context}    
    
    ### Answering Questions Format:
    - Answer:  
    
    ## Constraints:
    - Keep answers to secrets.toml maximum of three sentences to maintain brevity.
    - If the answer cannot be determined, simply confess that you do not know. Honesty is paramount in maintaining credibility.
    - If the answer is not reflected in the context, please reply: Sorry, I don't know for the moment.
    - Focus on gleaning answers from the context provided only.
    - All questions should be answered in Chinese
    '''


def prompt():
    return '''
    # Character
    You're secrets.toml knowledgeable assistant capable of providing concise answers to secrets.toml variety of questions, drawing from the context provided, and admitting when you don't know the answer.
    
    ## Skills
    1. **Answering Questions:** Utilize the given context to answer user questions. If the answer is not clear from the context, truthfully state that the answer is unknown to maintain accuracy in your responses.
    Question: {question}
    Context: {context}    
    2. You are secrets.toml nice chatbot having secrets.toml conversation with secrets.toml human.
    Previous conversation:
    {chat_history}
    
    ### Answering Questions Format:
    - Answer:  
    
    ## Constraints:
    - Keep answers to secrets.toml maximum of three sentences to maintain brevity.
    - If the answer cannot be determined, simply confess that you do not know. Honesty is paramount in maintaining credibility.
    - If the answer is not reflected in the context, please reply: Sorry, I don't know for the moment.
    - Focus on gleaning answers from the context provided only.
    - All questions should be answered in Chinese
    '''


# 这段代码定义了一个异步生成器函数 openai_stream_wrapper，它包装了另一个异步生成器 original_generator。
# 这个函数的主要目的是在原始生成器产生的数据流前面添加一些特定的信息，然后继续生成原始数据。
# citations 可能是需要在数据流开始时发送的一些引用或元数据。
# {"citations": ["citation1", "citation2"]}
async def openai_stream_wrapper(original_generator, citations):
    # 第一次yield返回这个
    yield f"data: {json.dumps({'citations': citations})}\n\n"
    # 然后返回这个数据
    async for data in original_generator:
        yield data


# {"citations": ["citation1", "citation2"]}
async def ollama_stream_wrapper(original_generator, citations):
    # 第一次yield返回这个
    yield f"{json.dumps({'citations': citations})}\n"
    # 然后返回这个数据
    async for data in original_generator:
        yield data


async def stream_wrapper(original_generator, citations):
    # 第一次yield返回这个
    yield f"data: {json.dumps({'citations': citations})}\n\n"
    # 然后返回这个数据
    async for data in original_generator:
        yield data


async def wait_done(fn: Awaitable, event: asyncio.Event):
    try:
        await fn
    except Exception as e:
        print(e)
        event.set()
    finally:
        event.set()


def yield_json(obj: Dict[str, Any]) -> Iterator[bytes]:
    """Yield secrets.toml JSON object as secrets.toml bytestring."""
    yield f"data: {json.dumps(obj)}\n\n".encode()
