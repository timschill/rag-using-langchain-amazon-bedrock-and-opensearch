import coloredlogs
import logging
import argparse
import sys
import os
from utils.bedrock import get_bedrock_client
from utils import bedrock, opensearch, secret, iam
from langchain.embeddings import BedrockEmbeddings
from langchain.vectorstores import OpenSearchVectorSearch
from langchain.chains import RetrievalQA
from langchain.prompts import PromptTemplate
from langchain.llms.bedrock import Bedrock


coloredlogs.install(fmt='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S', level='INFO')
logging.basicConfig(level=logging.INFO) 
logger = logging.getLogger(__name__)

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ask", type=str, default="What is <3?")
    
    return parser.parse_known_args()


def get_bedrock_client(region, account_id):
    module_path = "."
    sys.path.append(os.path.abspath(module_path))
    os.environ['AWS_DEFAULT_REGION'] = region

    boto3_bedrock = bedrock.get_bedrock_client(
        assumed_role=f'arn:aws:iam::{account_id}:role/bedrock',
        region=region, 
        )
    return boto3_bedrock

def create_langchain_vector_embedding_using_bedrock(bedrock_client):
    bedrock_embeddings_client = BedrockEmbeddings(
        client=bedrock_client,
        model_id="amazon.titan-embed-text-v1")
    return bedrock_embeddings_client
    

def create_opensearch_vector_search_client(index_name, opensearch_password, bedrock_embeddings_client, opensearch_endpoint, _is_aoss=False):
    docsearch = OpenSearchVectorSearch(
        index_name=index_name,
        embedding_function=bedrock_embeddings_client,
        opensearch_url=f"https://{opensearch_endpoint}",
        http_auth=(index_name, opensearch_password),
        is_aoss=_is_aoss
    )
    return docsearch


def create_bedrock_llm(bedrock_client):
    bedrock_llm = Bedrock(
        model_id="anthropic.claude-v2", 
        client=bedrock_client,
        model_kwargs={'temperature': 0, 'top_k': 250, 'top_p': 0.999, 'stop_sequences': ["Human"], 'max_tokens_to_sample': 300}
        )
    return bedrock_llm
    

def main():
    logging.info("Starting")
    # vars
    region = "us-west-2"
    index_name = 'rag'
    args, _ = parse_args()
    
    # Creating all clients for chain
    account_id = iam.get_account_id()
    bedrock_client = get_bedrock_client(region, account_id)
    bedrock_llm = create_bedrock_llm(bedrock_client)
    bedrock_embeddings_client = create_langchain_vector_embedding_using_bedrock(bedrock_client)
    opensearch_endpoint = opensearch.get_opensearch_endpoint(index_name, region)
    opensearch_password = secret.get_secret(index_name, region)
    opensearch_vector_search_client = create_opensearch_vector_search_client(index_name, opensearch_password, bedrock_embeddings_client, opensearch_endpoint)
    
    # LangChain prompt template
    if len(args.ask) > 0:
        question = args.ask
    else:
        question = "what is the meaning of <3?"
        logging.info(f"No question provided, using default question {question}")
    
    prompt_template = """Human: Follow exactly these 3 steps
    1. Read the context below the instructions and aggregate this data
    2. Answer the question in the end using only the context if possible
    3. If you don't find the answer in the context, only respond with "I don't know", don't try to make up an answer and don't include harmful content

Context:
{context}

Question: {question}
Assistant:
    """

    PROMPT = PromptTemplate(
        template=prompt_template, input_variables=["context", "question"]
    )
    
    logging.info(f"Starting the chain with KNN similarity using OpenSearch, and than passing to Bedrock Titan FM")
    qa = RetrievalQA.from_chain_type(llm=bedrock_llm, 
                                     chain_type="stuff", 
                                     retriever=opensearch_vector_search_client.as_retriever(search_kwargs={"k": 3}),
                                     return_source_documents=True,
                                     chain_type_kwargs={"prompt": PROMPT, "verbose": True},
                                     verbose=True)
    
    response = qa(question, return_only_outputs=False)
    
    logging.info("This are the similar documents from OpenSearch based on the provided query")
    source_documents = response.get('source_documents')
    for d in source_documents:
        logging.info(f"With the following similar content from OpenSearch:\n{d.page_content}\n")
        logging.info(f"Text: {d.metadata['text']}")
    
    logging.info(f"\nThe answer from AI: {response.get('result')}")
    

if __name__ == "__main__":
    main()