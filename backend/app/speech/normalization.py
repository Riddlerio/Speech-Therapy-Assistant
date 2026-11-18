"""인식 문장에서 한글 음절만 남기는 정규화 도우미.
공백·문장부호·영문이 섞여도 같은 문장으로 비교할 수 있게 판정·대화 근거 앞단에서 쓴다.
"""

import re
import unicodedata


def normalize(text: str | None) -> str | None:
    """유니코드 NFC로 맞춘 뒤 한글 음절(가~힣)만 남긴다. 남는 글자가 없으면 None이다.
    None을 돌려주면 판정에서는 무발화로 처리된다.
    """
    value = re.sub(r"[^가-힣]", "", unicodedata.normalize("NFC", text or ""))
    return value or None
