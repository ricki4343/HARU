from app import db

class Category(db.Model):
    __tablename__ = 'categories'
    
    category_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    category_name = db.Column(db.String(100), nullable=False, unique=True)
    transactions = db.relationship('Transaction', backref='category', lazy=True)

class Transaction(db.Model):
    __tablename__ = 'transactions'
    
    transaction_id = db.Column(db.String(50), primary_key=True)
    date = db.Column(db.Date, nullable=False)
    counterparty = db.Column(db.String(255), nullable=True)
    reference = db.Column(db.Text, nullable=True)
    direction = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    amount_absolute = db.Column(db.Numeric(10, 2), nullable=False)
    place = db.Column(db.String(100), nullable=True)
    note = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(20), nullable=False, default='bank_import')
    category_id = db.Column(db.Integer, db.ForeignKey('categories.category_id'), nullable=False)