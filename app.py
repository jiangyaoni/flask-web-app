from flask import Flask, request, render_template, redirect, url_for, session
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import redis
import datetime
import os

app = Flask(__name__)

# ==================== 1. 数据库配置（SQLite，免安装） ====================
# 获取当前文件所在目录，数据库文件会生成在项目根目录下
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
# SQLite 连接字符串（文件数据库，无需安装 MySQL）
# app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(BASE_DIR, "app.db")}'
# 如果你想用 MySQL，把上面这行注释掉，取消下面这行的注释，并修改账号密码/数据库名
# app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:123456@localhost:3306/flask_db'

# 部署实现
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(BASE_DIR, "app.db")}'


app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 初始化 SQLAlchemy
db = SQLAlchemy(app)


# ==================== 2. 用户模型（User） ====================
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)

    # 设置密码（自动加密）
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    # 校验密码
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


# ==================== 3. Flask 与 Session 配置 ====================
app.config['SECRET_KEY'] = 'your-secret-key-here-change-in-production'

# 为 Session 创建 Redis 连接（保持 decode_responses=False）
session_redis = redis.Redis(
    host='localhost',
    port=6379,
    db=1,
    decode_responses=False,
    protocol=2
)

app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_REDIS'] = session_redis
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_KEY_PREFIX'] = 'session:'

Session(app)

# ==================== 4. 业务 Redis 连接 (db=0) ====================
r = redis.Redis(
    host='localhost',
    port=6379,
    db=0,
    decode_responses=True,
    protocol=2
)


# ==================== 5. 辅助函数 ====================
def get_today_key(base_key):
    today = datetime.date.today().strftime('%Y-%m-%d')
    return f'{base_key}:{today}'


def get_user_id():
    """获取当前访客唯一标识：登录用户用 'user:用户名'，匿名用 IP+UA"""
    if 'username' in session:  # 注意：这里改为 'username'
        return f"user:{session['username']}"
    ip = request.remote_addr
    ua = request.headers.get('User-Agent', 'unknown')[:20]
    return f'anon:{ip}_{ua}'


def seconds_until_tomorrow():
    now = datetime.datetime.now()
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
    return int((tomorrow - now).total_seconds())


# ==================== 6. 统计逻辑 ====================
@app.before_request
def count_all_visits():
    if request.path in ['/login', '/logout', '/register'] or request.path.startswith('/static'):
        return
    r.incr('total_pv')
    today_pv_key = get_today_key('pv')
    r.incr(today_pv_key)
    r.expire(today_pv_key, seconds_until_tomorrow())
    today_uv_key = get_today_key('uv')
    user_id = get_user_id()
    r.sadd(today_uv_key, user_id)
    r.expire(today_uv_key, seconds_until_tomorrow())


# ==================== 7. 路由：首页 & 统计 ====================
@app.route('/')
def home():
    return render_template('home.html')


@app.route('/count')
def visit_count():
    today_pv_key = get_today_key('pv')
    today_uv_key = get_today_key('uv')
    today_pv = r.get(today_pv_key) or 0
    today_uv = r.scard(today_uv_key) or 0
    total_pv = r.get('total_pv') or 0
    current_user = get_user_id()
    return render_template(
        'count.html',
        today_pv=today_pv,
        today_uv=today_uv,
        total_pv=total_pv,
        current_user=current_user
    )


# ==================== 8. 路由：注册（核心新增） ====================
@app.route('/register', methods=['GET', 'POST'])
def register():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # 简单校验
        if not username or not password:
            error = '用户名和密码不能为空'
        else:
            # 检查用户名是否已存在
            existing_user = User.query.filter_by(username=username).first()
            if existing_user:
                error = '用户名已存在，请换一个'
            else:
                # 创建新用户并加密密码
                new_user = User(username=username)
                new_user.set_password(password)
                db.session.add(new_user)
                db.session.commit()
                # 注册成功后跳转到登录页
                return redirect(url_for('login'))

    return render_template('register.html', error=error)


# ==================== 9. 路由：登录（改造为查询数据库） ====================
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # 从数据库查询用户
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            # 登录成功，写入 Session（存储用户名即可）
            session['username'] = username
            return redirect(url_for('home'))
        else:
            error = '用户名或密码错误，请重试'

    return render_template('login.html', error=error)


# ==================== 10. 路由：登出 ====================
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))


# ==================== 11. 路由：个人中心（受保护） ====================
@app.route('/profile')
def profile():
    if 'username' not in session:
        return redirect(url_for('login'))
    username = session['username']
    return f'''
    <h1>👤 个人中心</h1>
    <p>欢迎回来，<strong>{username}</strong>！</p>
    <p>你已成功登录，这是只有登录用户才能看到的页面。</p>
    <p><a href="/">返回首页</a></p>
    '''


# ==================== 12. 创建数据库表（非常重要！） ====================
# 这一行确保在 Flask 启动前，如果表不存在则自动创建
with app.app_context():
    db.create_all()

# ==================== 13. 启动 ====================
if __name__ == '__main__':
    app.run(debug=True)