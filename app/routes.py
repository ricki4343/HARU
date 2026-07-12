from flask import Blueprint, jsonify, request, render_template
from app.models import Transaction, Category
from app import db
from app.llm import generate_sql, phrase_answer, is_safe_query, is_finance_question, conversational_reply
from sqlalchemy import text
from io import StringIO
from werkzeug.utils import secure_filename
import pandas as pd
import joblib
import os
import uuid
import re
import hashlib


model = joblib.load(os.path.join(os.path.dirname(__file__), '..', 'haru_model.pkl'))

main = Blueprint('main', __name__)

@main.route('/')
def index():
    return 'HARU is purring!'


@main.route('/chat')
def chat():
    return render_template('index.html')



@main.route('/transactions', methods=['GET'])
def get_transactions():
    
    # Optional query parameters for filtering
    direction = request.args.get('direction')      # 'expense' or 'income'
    category = request.args.get('category')        # e.g. 'groceries, parking, ...'
    place = request.args.get('place')              # e.g. 'Aachen, Düsseldorf, ...'
    source = request.args.get('source')            # for the manual input

    query = db.session.query(Transaction, Category)\
               .join(Category, Transaction.category_id == Category.category_id)

    if direction:
        query = query.filter(Transaction.direction == direction)
    if category:
        query = query.filter(Category.category_name == category)
    if place:
        query = query.filter(Transaction.place == place)
    if source:
        query = query.filter(Transaction.source == source)

    results = query.order_by(Transaction.date.desc()).limit(50).all()

    transactions = []
    for t, c in results:
        transactions.append({
            'transaction_id': t.transaction_id,
            'date': t.date.isoformat(),
            'counterparty': t.counterparty,
            'reference': t.reference,
            'direction': t.direction,
            'amount': float(t.amount),
            'amount_absolute': float(t.amount_absolute),
            'place': t.place,
            'source': t.source,
            'note': t.note,
            'category': c.category_name,
        })

    return jsonify({
        'count': len(transactions),
        'transactions': transactions
    })



@main.route('/add', methods=['POST'])
def add_transaction():
    data = request.get_json()

    # Required fields
    required = ['date', 'counterparty', 'amount', 'direction']
    for field in required:
        if field not in data:
            return jsonify({'error': f'Missing field: {field}'}), 400

    # Build text feature for model prediction
    counterparty = data.get('counterparty', '')
    reference = data.get('reference', '')
    feature = f"{counterparty} {reference}".lower().strip()

    # Predict category
    if 'category' in data:
        category_name = data['category']
    else:
        category_name = model.predict([feature])[0]

    # Look up category_id
    category = Category.query.filter_by(category_name=category_name).first()
    if not category:
        return jsonify({'error': f'Unknown category: {category_name}'}), 400

    # Generate transaction ID
    transaction = Transaction(
        transaction_id=str(uuid.uuid4()),
        date=data['date'],
        counterparty=counterparty,
        reference=reference,
        direction=data['direction'],
        amount=data['amount'],
        amount_absolute=abs(float(data['amount'])),
        place=data.get('place'),
        note=data.get('note'),
        source='manual',
        category_id=category.category_id
    )

    db.session.add(transaction)
    db.session.commit()

    return jsonify({
        'message': 'Transaction added successfully',
        'transaction_id': transaction.transaction_id,
        'predicted_category': category_name
    }), 201




@main.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()

    if 'question' not in data:
        return jsonify({'error': 'Missing field: question'}), 400

    question = data['question']
    question = data['question']

    # smalltalks vs finance query
    if not is_finance_question(question):
        answer = conversational_reply(question)
        return jsonify({
            'question': question,
            'answer': answer,
            'sql': None,
            'raw_result': None
        })

    # generate SQL from natural language
    sql = generate_sql(question)

    # check for UNABLE_TO_ANSWER
    if 'UNABLE_TO_ANSWER' in sql:
        return jsonify({
            'question': question,
            'answer': "I'm sorry, I can't answer that with the available data.",
            'sql': None
        })

    # validate, if query is safe
    if not is_safe_query(sql):
        return jsonify({'error': 'Generated query failed safety check'}), 400

    # run the query against real data
    try:
        with db.engine.connect() as connection:
            result = connection.execute(text(sql))
            rows = [dict(row._mapping) for row in result]
    except Exception as e:
        return jsonify({'error': f'Query execution failed: {str(e)}'}), 500

    # phrase the answer using the real result
    answer = phrase_answer(question, str(rows))

    return jsonify({
        'question': question,
        'answer': answer,
        'sql': sql,
        'raw_result': rows
    })



@main.route('/predict-category', methods=['POST'])
def predict_category():
    data = request.get_json()
    counterparty = data.get('counterparty', '')
    reference = data.get('reference', '')
    feature = f"{counterparty} {reference}".lower().strip()
    category = model.predict([feature])[0]
    return jsonify({'category': category})




@main.route('/upload-csv', methods=['POST'])
def upload_csv():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if not file.filename.endswith('.csv'):
        return jsonify({'error': 'File must be a CSV'}), 400

    try:
        content = file.read().decode('utf-8-sig')
        df = pd.read_csv(StringIO(content), sep=';', dtype=str)

        # ────────────────────────────────────────────────────────────── Validate required columns ──────────────────────────────
        required = {'date', 'counterparty', 'reference', 'category', 'place', 'amount'}
        missing = required - set(df.columns)
        if missing:
            return jsonify({'error': f'Missing columns: {missing}'}), 400

        # ────────────────────────────────────────────────────────────── Clean ──────────────────────────────────────────────────
        for col in df.select_dtypes(include='object').columns:
            df[col] = df[col].str.strip()

        df['date'] = pd.to_datetime(df['date'], format='%d.%m.%Y')

        def parse_amount(val):
            s = str(val).strip()
            s = re.sub(r'\.(?=\d{3}(?:,|$))', '', s)
            s = s.replace(',', '.')
            return float(s)

        df['amount'] = df['amount'].apply(parse_amount)
        df['amount_absolute'] = df['amount'].abs()
        df['direction'] = df['amount'].apply(
            lambda x: 'income' if x > 0 else 'expense'
        )

        # ────────────────────────────────────────────────────────────── Normalize categories ───────────────────────────────────
        CATEGORY_MAP = {'Car': 'car', 'Leisure/Entertainment': 'leisure'}
        df['category'] = df['category'].str.strip().replace(CATEGORY_MAP).str.lower()

        # ────────────────────────────────────────────────────────────── Build text feature and predict missing categories ────────────────────────────────────
        def build_feature(row):
            return f"{row['counterparty']} {row['reference']}".lower().strip()

        df['feature'] = df.apply(build_feature, axis=1)

        # Only predict where category is empty or 'others'
        needs_prediction = df['category'].isna() | (df['category'] == '')
        if needs_prediction.sum() > 0:
            df.loc[needs_prediction, 'category'] = model.predict(
                df.loc[needs_prediction, 'feature'].tolist()
            )

        # ──────────────────────────────── Insert transactions ────────────────────────────────────
        inserted = 0
        skipped = 0

        for _, row in df.iterrows():
            # Look up category
            category_obj = Category.query.filter_by(
                category_name=row['category']
            ).first()

            if not category_obj:
                category_obj = Category.query.filter_by(
                    category_name='others'
                ).first()

            # Generate transaction ID
            tx_id = hashlib.md5(
                f"{row['date']}|{row['counterparty']}|{row['amount']}|{_}".encode()
            ).hexdigest()

            # Skip if already exists
            exists = Transaction.query.filter_by(
                transaction_id=tx_id
            ).first()

            if exists:
                skipped += 1
                continue

            transaction = Transaction(
                transaction_id=tx_id,
                date=row['date'],
                counterparty=row['counterparty'],
                reference=row['reference'],
                direction=row['direction'],
                amount=row['amount'],
                amount_absolute=row['amount_absolute'],
                place=row['place'] if row['place'] != 'none' else None,
                note=None,
                source='bank_import',
                category_id=category_obj.category_id
            )
            db.session.add(transaction)
            inserted += 1

        db.session.commit()

        return jsonify({
            'inserted': inserted,
            'skipped': skipped,
            'total_rows': len(df)
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500



@main.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')



@main.route('/income')
def income_log():
    results = db.session.query(Transaction, Category)\
        .join(Category, Transaction.category_id == Category.category_id)\
        .filter(Transaction.direction == 'income')\
        .order_by(Transaction.date.desc())\
        .all()
    
    transactions = [{
        'date': t.date.strftime('%d.%m.%Y'),
        'counterparty': t.counterparty,
        'reference': t.reference,
        'amount': float(t.amount_absolute),
        'category': c.category_name,
        'place': t.place or '—',
        'source': t.source
    } for t, c in results]
    
    return render_template('log.html',
        title='💰 Income Log',
        transactions=transactions,
        total=sum(t['amount'] for t in transactions),
        direction='income'
    )



@main.route('/expenses')
def expenses_log():
    results = db.session.query(Transaction, Category)\
        .join(Category, Transaction.category_id == Category.category_id)\
        .filter(Transaction.direction == 'expense')\
        .order_by(Transaction.date.desc())\
        .all()
    
    transactions = [{
        'date': t.date.strftime('%d.%m.%Y'),
        'counterparty': t.counterparty,
        'reference': t.reference,
        'amount': float(t.amount_absolute),
        'category': c.category_name,
        'place': t.place or '—',
        'source': t.source
    } for t, c in results]
    
    return render_template('log.html',
        title='📉 Expenses Log',
        transactions=transactions,
        total=sum(t['amount'] for t in transactions),
        direction='expense'
    )


# debug function
@main.route('/debug-sql', methods=['POST'])
def debug_sql():
    data = request.get_json()
    question = data['question']
    sql = generate_sql(question)
    return jsonify({
        'question': question,
        'raw_llm_output': sql
    })