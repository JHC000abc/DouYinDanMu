# !/usr/bin/python3
# -*- coding:utf-8 -*-
"""
@author: JHC000abc@gmail.com
@file: models_db.py
@time: 2026/1/23 14:27 
@desc: 

"""
from pydantic import BaseModel


# 模型定义
class DBConfig(BaseModel):
    host: str
    port: str
    user: str
    password: str
    database: str
