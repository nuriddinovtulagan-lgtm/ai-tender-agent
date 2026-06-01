import os
import json
import re
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from fastapi import FastAPI
import gspread
from google.oauth2.service_account import Credentials

app = FastAPI(title="AI Tender Agent V2")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")


KEYWORDS = [
    "перевозка", "перевозка грузов", "услуги по перевозке",
    "транспортные услуги", "оказание транспортных услуг",
    "логистика", "логистические услуги",
    "экспедирование", "экспедиторские услуги",
    "транспортно-экспедиционные услуги",
    "доставка", "доставка груза", "доставка товаров",
    "грузоперевозка", "грузовые перевозки",
    "контейнер", "контейнерные перевозки",
    "таможенное оформление", "таможенные услуги",
    "склад", "складские услуги",
    "погрузка", "разгрузка",
    "погрузочно-разгрузочные работы",
    "спецтехника", "аренда спецтехники",
    "фура", "тягач", "полуприцеп", "рефрижератор",
    "freight", "cargo", "transport", "transportation",
    "logistics", "delivery", "shipping", "forwarding",
    "yuk tashish", "transport xizmati", "logistika",
    "yetkazib berish", "юк ташиш", "транспорт хизмати",
]


SEARCH_WORDS = [
    "перевозка грузов",
    "транспортные услуги",
    "логистика",
    "экспедирование",
    "доставка грузов",
    "контейнерные перевозки",
    "таможенное оформление",
    "погрузочно-разгрузочные работы",
    "yuk tashish",
    "transport xizmati",
    "logistika",
    "cargo transportation",
]


BAD_URL_PARTS = [
    "register", "login", "logout", "signin", "signup",
    "cabinet", "profile", "account", "user",
    "add.html", "add", "create", "new",
    "invited", "invitation",
    "english", "ru/", "uz/",
    "news", "blog", "faq", "help", "contact",
    "about", "rules", "terms", "privacy",
    "advertising", "banner",
    "calendar", "archive",
]


BAD_TITLE_WORDS = [
    "регистрация", "зарегистрироваться", "войти", "выход",
    "стать заказчиком", "стать поставщиком",
    "english", "русский", "ўзбекча",
    "приглашение", "мои заявки", "моих заявок",
    "вопрос", "ответ", "помощь",
    "новости", "контакты", "о сайте",
    "правила", "дата публикации",
    "закупки", "аренда", "поставка",
]


TENDER_URL_HINTS = [
    "tender", "lot", "lots", "auction", "procurement",
    "purchase", "zakup", "xarid", "etender", "tender-",
]


def clean_text(text):
    return " ".join((text or "").replace("\n", " ").replace("\t", " ").split())


def is_logistics_tender(text):
    text = (text or "").lower()
    return any(word.lower() in text for word in KEYWORDS)


def looks_like_bad_url(url):
    url = (url or "").lower()
    return any(part in url for part in BAD_URL_PARTS)


def looks_like_bad_title(title):
    title = (title or "").lower().strip()

    if len(title) < 12:
        return True

    if any(word in title for word in BAD_TITLE_WORDS):
        return True

    if title.isdigit():
        return True

    if len(title.split()) < 2:
        return True

    return False


def looks_like_tender_url(url):
    url = (url or "").lower()
    return any(hint in url for hint in TENDER_URL_HINTS)


def is_real_tender_candidate(title, url):
    title = clean_text(title)
    combined = f"{title} {url}".lower()

    if not title or not url:
        return False

    if looks_like_bad_url(url):
        return False

    if looks_like_bad_title(title):
        return False

    if not is_logistics_tender(combined):
        return False

    if not looks_like_tender_url(url) and len(title) < 25:
        return False

    return True
