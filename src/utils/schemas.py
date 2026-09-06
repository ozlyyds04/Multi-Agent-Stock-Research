from pydantic import BaseModel
from typing import List, Dict, Any, Optional


class PriceRecord(BaseModel):
    Date: str
    Open: float
    High: float
    Low: float
    Close: float
    Volume: float


class PriceBundle(BaseModel):
    symbol: str
    data: List[PriceRecord]
    meta: Dict[str, Any] = {}


class NewsItem(BaseModel):
    title: str
    link: str
    published: Optional[str] = None
    summary: Optional[str] = None
