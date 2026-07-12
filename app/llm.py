from openai import OpenAI
from dotenv import load_dotenv
import os
import re

load_dotenv()

client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

DB_SCHEMA = """
You have access to a MySQL database with the following tables:

TABLE transactions:
- transaction_id (VARCHAR): unique ID
- date (DATE): transaction date
- counterparty (VARCHAR): merchant or person name
- reference (TEXT): payment reference text
- direction (VARCHAR): 'expense' or 'income'
- amount (DECIMAL): signed amount, negative = expense
- amount_absolute (DECIMAL): always positive
- place (VARCHAR): city where transaction occurred, can be NULL
- note (TEXT): free text note, can be NULL
- source (VARCHAR): 'bank_import' or 'manual'
- category_id (INT): foreign key to categories

TABLE categories:
- category_id (INT): primary key
- category_name (VARCHAR): e.g. 'groceries', 'restaurant', 'café'

To get category names, always JOIN transactions with categories 
on category_id.
"""

SQL_SYSTEM_PROMPT = f"""
You are a SQL query generator for a personal finance database.

{DB_SCHEMA}

All currency is nominated in EUR.
Your job is to convert the user's natural language question into a single valid MySQL SELECT query.

Rules you must follow:
- Only generate SELECT statements, never INSERT, UPDATE, DELETE, DROP
- Always use amount_absolute for spending totals, never sum negative amounts
- Always JOIN categories when category name is needed
- Use LIMIT when needed: use the number the user requests, otherwise default to LIMIT 50
- Return ONLY the raw SQL query, no explanation, no markdown, no backticks
- Only return UNABLE_TO_ANSWER if the question requires data completely outside the schema

Here are examples of valid questions and queries:

User: How much did I spend on groceries in March 2026?
SQL: SELECT SUM(t.amount_absolute) AS total_spent FROM transactions t JOIN categories c ON t.category_id = c.category_id WHERE c.category_name = 'groceries' AND t.direction = 'expense' AND t.date >= '2026-03-01' AND t.date < '2026-04-01';

User: What were my top 5 spending categories in 2025?
SQL: SELECT c.category_name, SUM(t.amount_absolute) AS total_spent FROM transactions t JOIN categories c ON t.category_id = c.category_id WHERE t.direction = 'expense' AND YEAR(t.date) = 2025 GROUP BY c.category_name ORDER BY total_spent DESC LIMIT 5;

User: How many transactions did I make in Aachen?
SQL: SELECT COUNT(*) AS transaction_count FROM transactions WHERE place = 'Aachen';
"""

# ======================================================================== function 1: SQL generator ====================================

def generate_sql(user_question: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SQL_SYSTEM_PROMPT},
            {"role": "user", "content": user_question}
        ],
        temperature=0,
    )
    return response.choices[0].message.content.strip()


# ======================================================================== function 2: to phrase answer ====================================

def phrase_answer(user_question: str, query_result: str) -> str:
    prompt = f"""
The user asked: "{user_question}"

The database returned this result:
{query_result}

Write a short, very friendly, conversational answer in English based only on the data above. Assume that all currency is nominated in EUR.
Do not invent or assume any numbers not present in the result. 
If the result is empty, say no transactions were found.
"""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a helpful personal finance assistant"},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3,
    )
    return re.sub(r"\$","€", response.choices[0].message.content.strip())


# ======================================================================== function 3: the guardrail ====================================

FORBIDDEN_KEYWORDS = ['insert', 'update', 'delete', 'drop', 'alter', 'truncate', 'create']

def is_safe_query(sql: str) -> bool:
    sql_lower = sql.lower()
    for keyword in FORBIDDEN_KEYWORDS:
        if keyword in sql_lower:
            return False
    return sql_lower.strip().startswith('select')


# ======================================================================== function 4: smalltalks ====================================

def is_finance_question(user_question: str) -> bool:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": """You are a classifier. 
Decide if the user's message requires a database query about personal finance transactions.
Reply with exactly one word: YES or NO.
YES = questions about spending, income, transactions, categories, places, amounts, budgets.
NO = greetings, thank you, small talk, general questions unrelated to finance data."""},
            {"role": "user", "content": user_question}
        ],
        temperature=0,
    )
    return response.choices[0].message.content.strip().upper() == 'YES'


def conversational_reply(user_question: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": """You are HARU, a friendly personal finance assistant. 
You help users understand their spending and manage their household budget.
For greetings and small talk, respond warmly and briefly.
Remind the user they can ask you about their transactions, spending, or income."""},
            {"role": "user", "content": user_question}
        ],
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()