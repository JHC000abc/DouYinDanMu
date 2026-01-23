# !/usr/bin/python3
# -*- coding:utf-8 -*-
"""
@author: JHC000abc@gmail.com
@file: models_dm.py
@time: 2026/1/23 14:28 
@desc: 

"""
from models.model_base import Base
from sqlalchemy import Column, Integer, String, TIMESTAMP, Text
from datetime import datetime




class LiveDanmakuModel(Base):
    __tablename__ = "live_danmaku"
    id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(String(50), index=True)
    user_nick = Column(String(100))
    user_uid = Column(String(50))
    display_id = Column(String(50))
    content = Column(Text)
    gender = Column(String(10))
    avatar_url = Column(Text)
    msg_type = Column(String(20))
    gift_id = Column(String(50))
    gift_count = Column(Integer)
    capture_time = Column(TIMESTAMP, default=datetime.now)
