# 1. 导入 Flask 类
from flask import Flask

# 2. 创建 Flask 应用实例
app = Flask(__name__)


# 3. 定义路由和视图函数
@app.route('/')
def hello_world():
    s = """
        <h1>Hello, World!</h1>
        <p>第一次尝试Flask</p>
    """
    return s


# 4. 启动应用
if __name__ == '__main__':
    app.run(debug=True)
