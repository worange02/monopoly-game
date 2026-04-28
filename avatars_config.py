# avatars_config.py
# 头像列表 - 从images文件夹读取
AVATARS = [
    {"id": "xiaogou", "name": "小狗", "file": "小狗.jpg", "emoji": "🐶"},
    {"id": "xiaotu", "name": "小兔", "file": "小兔.jpg", "emoji": "🐰"},
]

# 聊天表情包 GIF 列表 (1-19.gif)
CHAT_EMOJIS = [f"{i}.gif" for i in range(1, 20)]