from flask import Flask, request, render_template, redirect, url_for, session
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import redis
import datetime
import os

# ==================== 0. 应用初始化 ====================
app = Flask(__name__)

# ==================== 1. 数据库配置 ====================
# 从环境变量读取数据库地址（Render 上自动使用 PostgreSQL）
database_url = os.environ.get('DATABASE_URL')
if database_url:
    # Render 提供的 postgres:// 需转为 postgresql://（SQLAlchemy 要求）
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # 本地开发：使用 SQLite
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(BASE_DIR, "app.db")}'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


# ==================== 2. 用户模型 ====================
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    nickname = db.Column(db.String(80), default='')  # 新增：昵称
    avatar = db.Column(db.String(200), default='')  # 新增：头像路径
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


# ==================== 3. Session 与 Redis 配置 ====================
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# 从环境变量读取 Redis 地址
redis_url = os.environ.get('REDIS_URL')

if redis_url:
    # 线上环境：使用环境变量中的 Redis
    session_redis = redis.Redis.from_url(redis_url, decode_responses=False, protocol=2)
    r = redis.Redis.from_url(redis_url, decode_responses=True, protocol=2)
else:
    # 本地开发：使用 localhost
    session_redis = redis.Redis(
        host='localhost',
        port=6379,
        db=1,
        decode_responses=False,
        protocol=2
    )
    r = redis.Redis(
        host='localhost',
        port=6379,
        db=0,
        decode_responses=True,
        protocol=2
    )

app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_REDIS'] = session_redis
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_KEY_PREFIX'] = 'session:'

Session(app)


# ==================== 4. 辅助函数 ====================
def get_today_key(base_key):
    today = datetime.date.today().strftime('%Y-%m-%d')
    return f'{base_key}:{today}'


def get_user_id():
    """获取当前访客唯一标识：登录用户用 'user:账号'，匿名用 IP+UA"""
    if 'username' in session:
        return f"user:{session['username']}"
    ip = request.remote_addr
    ua = request.headers.get('User-Agent', 'unknown')[:20]
    return f'anon:{ip}_{ua}'


def seconds_until_tomorrow():
    now = datetime.datetime.now()
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
    return int((tomorrow - now).total_seconds())


# ==================== 5. 统计逻辑 ====================
@app.before_request
def count_all_visits():
    # 排除静态资源、登录/注册/登出页面，避免干扰统计
    if request.path in ['/login', '/logout', '/register', '/edit_profile'] or request.path.startswith('/static'):
        return

    # 历史总 PV
    r.incr('total_pv')

    # 今日 PV
    today_pv_key = get_today_key('pv')
    r.incr(today_pv_key)
    r.expire(today_pv_key, seconds_until_tomorrow())

    # 今日 UV（Set 去重）
    today_uv_key = get_today_key('uv')
    user_id = get_user_id()
    r.sadd(today_uv_key, user_id)
    r.expire(today_uv_key, seconds_until_tomorrow())


# ==================== 6. 路由：首页 & 统计 ====================
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


# ==================== 7. 路由：注册 ====================
@app.route('/register', methods=['GET', 'POST'])
def register():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            error = '账号和密码不能为空'
        else:
            existing_user = User.query.filter_by(username=username).first()
            if existing_user:
                error = '账号已存在，请换一个'
            else:
                new_user = User(username=username)
                new_user.set_password(password)
                db.session.add(new_user)
                db.session.commit()
                return redirect(url_for('login'))
    return render_template('register.html', error=error)


# ==================== 8. 路由：登录 ====================
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session['username'] = username
            return redirect(url_for('home'))
        else:
            error = '账号或密码错误，请重试'
    return render_template('login.html', error=error)


# ==================== 9. 路由：登出 ====================
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))


# ==================== 10. 路由：个人中心 ====================
@app.route('/profile')
def profile():
    if 'username' not in session:
        return redirect(url_for('login'))
    username = session['username']
    user = User.query.filter_by(username=username).first()
    return render_template('profile.html', user=user)


# ==================== 11. 路由：编辑资料（修改昵称和头像） ====================
# 文件上传配置
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/edit_profile', methods=['GET', 'POST'])
def edit_profile():
    if 'username' not in session:
        return redirect(url_for('login'))

    username = session['username']
    user = User.query.filter_by(username=username).first()

    if not user:
        return redirect(url_for('logout'))

    error = None

    if request.method == 'POST':
        # 1. 处理昵称
        nickname = request.form.get('nickname', '').strip()
        user.nickname = nickname

        # 2. 处理头像上传
        if 'avatar' in request.files:
            file = request.files['avatar']
            if file and file.filename != '':
                if allowed_file(file.filename):
                    # 生成安全的文件名
                    filename = secure_filename(file.filename)
                    name, ext = filename.rsplit('.', 1)
                    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                    new_filename = f"{name}_{timestamp}.{ext}"

                    # 保存文件
                    file_path = os.path.join(UPLOAD_FOLDER, new_filename)
                    file.save(file_path)

                    # 删除旧头像
                    if user.avatar:
                        old_path = os.path.join(UPLOAD_FOLDER, user.avatar)
                        if os.path.exists(old_path):
                            os.remove(old_path)

                    user.avatar = new_filename
                else:
                    error = '不支持的文件格式，请上传 JPG、PNG 或 GIF'
                    # 有错误时不提交，留在编辑页
                    return render_template('edit_profile.html', user=user, error=error)

        # 如果没有错误，提交数据库并跳转到个人中心
        if not error:
            db.session.commit()
            return redirect(url_for('profile'))

    # GET 请求或出错时显示编辑页面
    return render_template('edit_profile.html', user=user, error=error)


# ==================== 12. 创建数据库表 ====================
with app.app_context():
    db.create_all()

# ==================== 13. 启动 ====================
if __name__ == '__main__':
    app.run(debug=True)