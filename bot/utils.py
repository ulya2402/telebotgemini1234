import logging
from aiogram.filters.base import Filter
from aiogram.types import Message
from typing import Union, List as TypingList, Set as TypingSet

_ESCAPE_MD_V1_CHARS = r'_*`[' 

def local_escape_markdown_v1(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    temp_text = text
    for char_to_escape in _ESCAPE_MD_V1_CHARS:
        temp_text = temp_text.replace(char_to_escape, f"\\{char_to_escape}")
    return temp_text

class ManualChatTypeFilter(Filter):
    def __init__(self, chat_type: Union[str, TypingList[str], TypingSet[str]]):
        if isinstance(chat_type, str):
            self.chat_types: TypingSet[str] = {chat_type}
        else:
            self.chat_types: TypingSet[str] = set(chat_type)

    async def __call__(self, message: Message) -> bool:
        if not message.chat:
            return False
        return message.chat.type in self.chat_types


def split_long_message(text: str, max_length: int) -> TypingList[str]:
    """
    Memecah teks panjang menjadi beberapa bagian yang lebih kecil,
    mencoba memecah pada baris baru jika memungkinkan.
    """
    if not text:
        return []

    chunks: TypingList[str] = []
    current_pos = 0
    text_len = len(text)

    while current_pos < text_len:
        end_pos = current_pos + max_length
        if end_pos >= text_len:
            chunks.append(text[current_pos:])
            break

        split_at = -1

        rfind_double_newline = text.rfind('\n\n', current_pos, end_pos)
        if rfind_double_newline > current_pos: 
            split_at = rfind_double_newline + 2 
        else:
            rfind_single_newline = text.rfind('\n', current_pos, end_pos)
            if rfind_single_newline > current_pos:
                split_at = rfind_single_newline + 1 
            else:
                rfind_space = text.rfind(' ', current_pos, end_pos)
                if rfind_space > current_pos:
                    split_at = rfind_space + 1 
                else:
                    split_at = end_pos

        chunks.append(text[current_pos:split_at])
        current_pos = split_at

        while current_pos < text_len and text[current_pos].isspace():
            current_pos += 1

    return [chunk for chunk in chunks if chunk]
