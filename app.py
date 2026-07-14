from flask import Flask, request, render_template, redirect, url_for, session
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from functools import wraps
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy.exc import IntegrityError
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
migrate = Migrate(app, db)

# ==================== 2. 用户模型 ====================
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    nickname = db.Column(db.String(80), default='')
    avatar = db.Column(db.String(200), default='')
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


# ==================== 文章模型 ====================
class Post(db.Model):
    __tablename__ = 'posts'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    author = db.relationship('User', backref=db.backref('posts', lazy=True))

    def __repr__(self):
        return f'<Post {self.title}>'


# ==================== 分类模型 ====================
class Category(db.Model):
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    slug = db.Column(db.String(50), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)
    posts = db.relationship('Post', backref='category', lazy=True)

    def __repr__(self):
        return f'<Category {self.name}>'


# ==================== 标签模型 ====================
class Tag(db.Model):
    __tablename__ = 'tags'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    slug = db.Column(db.String(50), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)
    posts = db.relationship('Post', secondary='post_tags', backref='tags', lazy=True)

    def __repr__(self):
        return f'<Tag {self.name}>'


# ==================== 文章-标签关联表 ====================
post_tags = db.Table(
    'post_tags',
    db.Column('post_id', db.Integer, db.ForeignKey('posts.id'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('tags.id'), primary_key=True)
)


# ==================== 3. Session 与 Redis 配置 ====================
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

redis_url = os.environ.get('REDIS_URL')

if redis_url:
    session_redis = redis.Redis.from_url(redis_url, decode_responses=False, protocol=2)
    r = redis.Redis.from_url(redis_url, decode_responses=True, protocol=2)
else:
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
    if 'username' in session:
        return f"user:{session['username']}"
    ip = request.remote_addr
    ua = request.headers.get('User-Agent', 'unknown')[:20]
    return f'anon:{ip}_{ua}'


def seconds_until_tomorrow():
    now = datetime.datetime.now()
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
    return int((tomorrow - now).total_seconds())


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# ==================== 5. 统计逻辑 ====================
@app.before_request
def count_all_visits():
    if request.path in ['/login', '/logout', '/register', '/edit_profile'] or request.path.startswith('/static'):
        return

    r.incr('total_pv')

    today_pv_key = get_today_key('pv')
    r.incr(today_pv_key)
    r.expire(today_pv_key, seconds_until_tomorrow())

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
        username = request.form.get('username', '').strip()  # 去除首尾空格
        password = request.form.get('password')
        if not username or not password:
            error = '账号和密码不能为空'
        else:
            new_user = User(username=username)
            new_user.set_password(password)
            db.session.add(new_user)
            try:
                db.session.commit()
                return redirect(url_for('login'))
            except IntegrityError:
                db.session.rollback()
                error = '账号已存在，请换一个'
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
@login_required
def profile():
    username = session['username']
    user = User.query.filter_by(username=username).first()
    return render_template('profile.html', user=user)


# ==================== 11. 路由：编辑资料 ====================
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    username = session['username']
    user = User.query.filter_by(username=username).first()

    if not user:
        return redirect(url_for('logout'))

    error = None

    if request.method == 'POST':
        nickname = request.form.get('nickname', '').strip()
        user.nickname = nickname

        if 'avatar' in request.files:
            file = request.files['avatar']
            if file and file.filename != '':
                if allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    name, ext = filename.rsplit('.', 1)
                    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                    new_filename = f"{name}_{timestamp}.{ext}"
                    file_path = os.path.join(UPLOAD_FOLDER, new_filename)
                    file.save(file_path)

                    if user.avatar:
                        old_path = os.path.join(UPLOAD_FOLDER, user.avatar)
                        if os.path.exists(old_path):
                            os.remove(old_path)
                    user.avatar = new_filename
                else:
                    error = '不支持的文件格式，请上传 JPG、PNG 或 GIF'
                    return render_template('edit_profile.html', user=user, error=error)

        if not error:
            db.session.commit()
            return redirect(url_for('profile'))

    return render_template('edit_profile.html', user=user, error=error)


# ==================== 12. 路由：博客 ====================
@app.route('/blog')
def blog_index():
    page = request.args.get('page', 1, type=int)
    posts = Post.query.order_by(Post.created_at.desc()).paginate(page=page, per_page=10)
    categories = Category.query.all()
    tags = Tag.query.all()
    return render_template('blog/index.html', posts=posts, categories=categories, tags=tags)


@app.route('/blog/<int:post_id>')
def blog_detail(post_id):
    post = Post.query.get_or_404(post_id)

    r.incr(f'post:{post.id}:views')
    visitor = get_user_id()
    r.sadd(f'post:{post.id}:visitors', visitor)

    pv = r.get(f'post:{post.id}:views') or 0
    uv = r.scard(f'post:{post.id}:visitors') or 0

    return render_template('blog/detail.html', post=post, pv=pv, uv=uv)


@app.route('/blog/create', methods=['GET', 'POST'])
@login_required
def blog_create():
    categories = Category.query.all()
    tags = Tag.query.all()

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        category_id = request.form.get('category_id', type=int)
        tag_ids = request.form.getlist('tag_ids', type=int)

        if not title or not content:
            error = '标题和内容不能为空'
            return render_template('blog/create.html', error=error, categories=categories, tags=tags)

        user = User.query.filter_by(username=session['username']).first()
        if not user:
            return redirect(url_for('logout'))

        post = Post(title=title, content=content, author_id=user.id, category_id=category_id)

        if tag_ids:
            selected_tags = Tag.query.filter(Tag.id.in_(tag_ids)).all()
            post.tags.extend(selected_tags)

        db.session.add(post)
        db.session.commit()
        return redirect(url_for('blog_detail', post_id=post.id))

    return render_template('blog/create.html', categories=categories, tags=tags)


@app.route('/blog/<int:post_id>/edit', methods=['GET', 'POST'])
@login_required
def blog_edit(post_id):
    post = Post.query.get_or_404(post_id)
    categories = Category.query.all()
    tags = Tag.query.all()

    user = User.query.filter_by(username=session['username']).first()
    if not user or post.author_id != user.id:
        return redirect(url_for('blog_detail', post_id=post.id))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        category_id = request.form.get('category_id', type=int)
        tag_ids = request.form.getlist('tag_ids', type=int)

        if not title or not content:
            error = '标题和内容不能为空'
            return render_template('blog/edit.html', post=post, error=error, categories=categories, tags=tags)

        post.title = title
        post.content = content
        post.category_id = category_id

        post.tags.clear()
        if tag_ids:
            selected_tags = Tag.query.filter(Tag.id.in_(tag_ids)).all()
            post.tags.extend(selected_tags)

        db.session.commit()
        return redirect(url_for('blog_detail', post_id=post.id))

    selected_tag_ids = [tag.id for tag in post.tags]
    return render_template('blog/edit.html', post=post, categories=categories, tags=tags, selected_tag_ids=selected_tag_ids)


@app.route('/blog/<int:post_id>/delete', methods=['POST'])
@login_required
def blog_delete(post_id):
    post = Post.query.get_or_404(post_id)

    user = User.query.filter_by(username=session['username']).first()
    if not user or post.author_id != user.id:
        return redirect(url_for('blog_detail', post_id=post.id))

    db.session.delete(post)
    db.session.commit()
    return redirect(url_for('blog_index'))


# ==================== 13. 路由：分类筛选 ====================
@app.route('/blog/category/<slug>')
def blog_category(slug):
    category = Category.query.filter_by(slug=slug).first_or_404()
    page = request.args.get('page', 1, type=int)
    posts = Post.query.filter_by(category_id=category.id)\
        .order_by(Post.created_at.desc())\
        .paginate(page=page, per_page=10)
    categories = Category.query.all()
    tags = Tag.query.all()
    return render_template('blog/index.html', posts=posts, title=f'分类：{category.name}', categories=categories, tags=tags)


# ==================== 14. 路由：标签筛选 ====================
@app.route('/blog/tag/<slug>')
def blog_tag(slug):
    tag = Tag.query.filter_by(slug=slug).first_or_404()
    page = request.args.get('page', 1, type=int)
    # 通过关联查询：筛选出包含该标签的文章
    posts = Post.query.join(Post.tags).filter(Tag.id == tag.id)\
        .order_by(Post.created_at.desc())\
        .paginate(page=page, per_page=10)
    categories = Category.query.all()
    tags = Tag.query.all()
    return render_template('blog/index.html', posts=posts, title=f'标签：{tag.name}', categories=categories, tags=tags)


# ==================== 15. 路由：搜索 ====================
@app.route('/blog/search')
def blog_search():
    keyword = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)

    if not keyword:
        return redirect(url_for('blog_index'))

    posts = Post.query.filter(
        db.or_(
            Post.title.ilike(f'%{keyword}%'),
            Post.content.ilike(f'%{keyword}%')
        )
    ).order_by(Post.created_at.desc()).paginate(page=page, per_page=10)

    categories = Category.query.all()
    tags = Tag.query.all()
    return render_template('blog/index.html', posts=posts, title=f'搜索：{keyword}', keyword=keyword, categories=categories, tags=tags)


# ==================== 16. 初始化示例数据（仅首次使用） ====================
@app.route('/blog/init_data')
@login_required
def blog_init_data():
    if Category.query.count() > 0:
        return "数据已存在，无需初始化"

    categories = [
        Category(name='技术', slug='tech'),
        Category(name='生活', slug='life'),
        Category(name='随笔', slug='essay'),
    ]
    db.session.add_all(categories)

    tags = [
        Tag(name='Flask', slug='flask'),
        Tag(name='Python', slug='python'),
        Tag(name='Redis', slug='redis'),
        Tag(name='部署', slug='deploy'),
        Tag(name='前端', slug='frontend'),
    ]
    db.session.add_all(tags)

    db.session.commit()
    return "✅ 示例分类和标签已创建！<a href='/blog'>去博客看看</a>"


# ==================== 17. 创建数据库表 ====================
with app.app_context():
    db.create_all()

# ==================== 18. 启动 ====================
if __name__ == '__main__':
    app.run(debug=True)