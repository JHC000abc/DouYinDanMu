# !/usr/bin/python3
# -*- coding:utf-8 -*-
"""
@author: JHC000abc@gmail.com
@file: utils_db.py
@time: 2026/1/24 01:23 
@desc: 

"""
import os
import json
from models.models_dm import Base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine,text


engine = None
SessionLocal = None

DB_CONFIG_FILE = "config/db_config.json"
# 默认数据库配置
DEFAULT_DB_CONFIG = {
    "host": "127.0.0.1",
    "port": "3306",
    "user": "root",
    "password": "",
    "database": "douyin_monitor"
}


def load_db_config():
    """加载数据库配置，不存在则创建默认"""
    if not os.path.exists(DB_CONFIG_FILE):
        # 确保目录存在
        os.makedirs(os.path.dirname(DB_CONFIG_FILE), exist_ok=True)
        save_db_config(DEFAULT_DB_CONFIG)
        return DEFAULT_DB_CONFIG
    try:
        with open(DB_CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return DEFAULT_DB_CONFIG


def save_db_config(config):
    """保存数据库配置到文件"""
    if hasattr(config, 'dict'):
        config = config.dict()
    # 确保目录存在
    os.makedirs(os.path.dirname(DB_CONFIG_FILE), exist_ok=True)
    with open(DB_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)


def get_db_url(config):
    """拼接数据库连接字符串"""
    return f"mysql+pymysql://{config['user']}:{config['password']}@{config['host']}:{config['port']}/{config['database']}?charset=utf8mb4"


def init_db_engine():
    """初始化或重置数据库引擎"""
    global engine, SessionLocal

    config = load_db_config()
    db_url = get_db_url(config)

    try:
        if engine:
            engine.dispose()

        print(f"🔄 正在连接数据库: {config['host']}:{config['port']} ({config['database']})...")
        engine = create_engine(db_url, pool_pre_ping=True, pool_size=20, max_overflow=20)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

        # 测试连接并自动建表
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            print("✅ 数据库连接成功")
            Base.metadata.create_all(bind=engine)

    except Exception as e:
        print(f"⚠️ 数据库连接失败: {e}")
        print("💡 提示: 请在网页控制台的'系统设置'中配置正确的数据库信息。")
        engine = None
        SessionLocal = None



init_db_engine()