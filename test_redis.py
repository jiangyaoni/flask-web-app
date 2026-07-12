import redis

# 连接配置
# 如果 Redis 服务端是 5.x 版本，必须加 protocol=2，否则会报错 unknown command 'HELLO'
r = redis.Redis(
    host='localhost',  # Redis 服务地址
    port=6379,  # 端口号（与 redis.windows.conf 中一致）
    db=0,  # 数据库编号（0-15）
    decode_responses=True,  # 自动将返回结果转为字符串（否则是 b'xxx' 字节格式）
    protocol=2  # 🔥 关键！兼容 Redis 5.x 旧版协议
)

try:
    # 写入一个键值对（String 类型）
    r.set('test_key', 'Hello Redis!')

    # 读取这个键值对
    value = r.get('test_key')

    print("✅ Redis 连接成功！")
    print(f"读取到的内容：{value}")

except Exception as e:
    print("❌ 连接失败，请检查 Redis 服务是否启动")
    print(f"报错信息：{e}")