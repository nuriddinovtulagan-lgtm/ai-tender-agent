"""Letter templates for the Sales agent (approved by the owner on 22.10.2026).

Each template is (subject, body). The body may contain {greeting}. Edit the text here;
the agent picks a template by company type and country (see sales.choose_template).
"""

from __future__ import annotations

from typing import Dict, Tuple

RU_SIGNATURE = """С уважением,
Нуриддинов Т. А.
Директор, Trans Ocean Logistics
Тел. / Telegram / WhatsApp: +998 98 305-72-34
Тел.: +998 99 898-12-34
E-mail: ocean_logistics@mail.ru"""

EN_SIGNATURE = """Best regards,
T. A. Nuriddinov
Director, Trans Ocean Logistics
Phone / Telegram / WhatsApp: +998 98 305 72 34
Phone: +998 99 898 12 34
E-mail: ocean_logistics@mail.ru"""

RU_GREETING = "Уважаемые господа!"
EN_GREETING = "Dear {company} team,"

FORWARDER_RU = ("Предложение о сотрудничестве: автомобильные грузоперевозки по Узбекистану", """{greeting}

Компания Trans Ocean Logistics (г. Ташкент) предлагает Вам сотрудничество в области автомобильных грузоперевозок по территории Республики Узбекистан.

О компании
Мы работаем на рынке грузоперевозок с 2015 года. Наши заказчики — крупные государственные и частные организации; большая часть контрактов заключена по итогам открытых тендеров. Это подтверждает соответствие компании строгим требованиям к срокам, качеству и документальному сопровождению перевозок.

Среди наших заказчиков:
• АК «Узбектелеком» — с 2018 года по настоящее время;
• мобильный оператор Uzmobile — 2016–2022 гг.;
• ООО «Таъминот ва логистика хизмати» — с 2023 года по настоящее время;
• ОРП проекта «Развитие первичной медико-санитарной помощи» (Азиатский банк развития, ПКМ-902) — с 2025 года по настоящее время;
• UzMedImpeks — 2019–2023 гг.;
• сотовый оператор Mobiuz — 2022–2024 гг.;
• ГП «Навоийуран» — 2024 г.;
• сотовый оператор Ucell;
• ГУ «Центр закупок при Министерстве здравоохранения Республики Узбекистан»;
• ООО «Davlat belgisi».

Собственный автопарк — 20 единиц грузовой техники:
• низкорамные тралы грузоподъёмностью от 1,5 до 50 т — тяжеловесные и негабаритные грузы, спецтехника, промышленное оборудование;
• тентованные полуприцепы объёмом 96 м³ — паллетированные, тарно-штучные и сборные грузы;
• контейнеровозы — перевозка морских контейнеров;
• бортовые полуприцепы — металлоконструкции, строительные материалы, длинномерные грузы.
Для крупных заказов привлекаем транспорт проверенных партнёров.

Что мы перевозим: промышленное и энергетическое оборудование, медицинское оборудование, спецтехнику, металлоконструкции, строительные материалы, телекоммуникационное оборудование и антенно-мачтовые сооружения, контейнеры и генеральные грузы.

Предлагаем Вашей компании:
• доставку грузов Ваших клиентов от пограничных переходов, железнодорожных станций, терминалов и складов до объектов в любом регионе Узбекистана;
• вывоз и доставку контейнеров;
• перевозку тяжеловесных и негабаритных грузов;
• полный комплект перевозочных документов.

Будем рады рассчитать ставки по Вашим направлениям и встретиться в удобное для Вас время.

""" + RU_SIGNATURE)

FORWARDER_EN = ("Partnership proposal: road freight transport across Uzbekistan", """{greeting}

Trans Ocean Logistics (Tashkent) would like to offer your company a partnership in road freight transport across the Republic of Uzbekistan.

About us
We have been operating in the freight market since 2015. Our customers are major public and private organisations, and most of our contracts have been awarded through open tenders. This confirms that we meet strict requirements for delivery times, service quality and transport documentation.

Our customers include:
• Uzbektelecom JSC — since 2018;
• mobile operator Uzmobile — 2016–2022;
• Ta'minot va Logistika Xizmati LLC — since 2023;
• the Project Implementation Unit of the Primary Health Care Development project (Asian Development Bank) — since 2025;
• UzMedImpeks — 2019–2023;
• mobile operator Mobiuz — 2022–2024;
• Navoiyuran State Enterprise — 2024;
• mobile operator Ucell;
• the Procurement Center under the Ministry of Health of the Republic of Uzbekistan;
• Davlat Belgisi LLC.

Own fleet — 20 heavy vehicles:
• lowbed trailers with a payload from 1.5 to 50 t — heavy and oversized cargo, machinery, industrial equipment;
• 96 m³ curtain-side semi-trailers — palletised, packaged and groupage cargo;
• container chassis — sea containers;
• flatbed semi-trailers — steel structures, building materials, long cargo.
For large orders we add vehicles from vetted partner carriers.

We transport: industrial and power equipment, medical equipment, machinery, steel structures, building materials, telecom equipment and tower structures, containers and general cargo.

We offer your company:
• delivery of your clients' cargo from border crossings, railway stations, terminals and warehouses to sites in any region of Uzbekistan;
• container haulage;
• transport of heavy and oversized cargo;
• a full set of transport documents.

We would welcome the opportunity to quote rates for your routes and would be glad to meet at a time convenient for you.

""" + EN_SIGNATURE)

CARRIER_RU = ("Партнёр в Узбекистане: доставка по регионам, перегруз и негабарит", """{greeting}

Компания Trans Ocean Logistics (г. Ташкент) с 2015 года выполняет автомобильные перевозки по всей территории Узбекистана и предлагает Вам сотрудничество на конечном участке Ваших перевозок.

Ваши машины часто разгружаются в Ташкенте или Самарканде, тогда как получатель находится в другом регионе. Мы готовы взять этот участок на себя:
• доставка после таможенного оформления в любой город и на любой объект Узбекистана;
• перегруз с Вашего транспорта на наш, если груз следует дальше или требует другого типа прицепа;
• перевозка тяжеловесных и негабаритных грузов на низкорамных тралах грузоподъёмностью от 1,5 до 50 т;
• вывоз контейнеров и оформление перевозочных документов;
• обратные загрузки из Узбекистана для Ваших машин.

Собственный автопарк — 20 единиц техники: тралы, тентованные полуприцепы 96 м³, контейнеровозы и бортовые полуприцепы. При необходимости привлекаем транспорт проверенных партнёров.

Среди наших заказчиков — АК «Узбектелеком», Uzmobile, Ucell, Mobiuz, ГП «Навоийуран», UzMedImpeks, Центр закупок при Министерстве здравоохранения, ООО «Davlat belgisi» и проект Азиатского банка развития в сфере здравоохранения; большинство контрактов заключено по итогам открытых тендеров.

Направьте, пожалуйста, Ваши типовые маршруты и объёмы — мы подготовим ставки в течение одного рабочего дня.

""" + RU_SIGNATURE)

CARRIER_EN = ("Your partner in Uzbekistan: regional delivery, transloading and oversized cargo", """{greeting}

Trans Ocean Logistics is a Tashkent-based road carrier operating across Uzbekistan since 2015. We would like to offer your company a partnership for the final leg of your shipments within the country.

Your trucks often unload in Tashkent or Samarkand, while the consignee is located in another region. We can take over this part of the route:
• onward delivery after customs clearance to any city or project site in Uzbekistan;
• transloading from your truck to ours when the cargo travels further or requires a different trailer;
• heavy and oversized cargo on our lowbed trailers (payload from 1.5 to 50 t);
• container haulage and local transport documentation;
• backloads from Uzbekistan for your returning trucks.

Our own fleet consists of 20 heavy vehicles with lowbed, 96 m³ curtain-side, container and flatbed trailers, supported by a network of vetted partner carriers.

Our customers include Uzbektelecom JSC, Uzmobile, Ucell, Mobiuz, Navoiyuran State Enterprise, UzMedImpeks, the Procurement Center under the Ministry of Health, Davlat Belgisi LLC and an Asian Development Bank-financed health care project — most of these contracts were awarded through open tenders.

If this is of interest, please send us your typical routes and volumes, and we will reply with rates within one working day.

""" + EN_SIGNATURE)

FOLLOWUP_RU = ("Re: {subject}", """{greeting}

Несколько дней назад мы направили Вам предложение о сотрудничестве по автомобильным перевозкам внутри Узбекистана. Хотели бы уточнить, актуально ли оно для Вашей компании.

Кратко о нас: Trans Ocean Logistics работает с 2015 года, собственный парк — 20 единиц техники (низкорамные тралы от 1,5 до 50 т, тентованные полуприцепы 96 м³, контейнеровозы, бортовые полуприцепы). Будем рады рассчитать ставки по Вашим маршрутам — достаточно ответить на это письмо или написать нам в WhatsApp / Telegram.

""" + RU_SIGNATURE)

FOLLOWUP_EN = ("Re: {subject}", """{greeting}

A few days ago we sent you a partnership proposal for road transport within Uzbekistan. We would like to check whether it is of interest to your company.

In short: Trans Ocean Logistics has been operating since 2015 with its own fleet of 20 heavy vehicles (lowbeds from 1.5 to 50 t, 96 m³ curtain-siders, container chassis, flatbeds). We would be glad to quote rates for your routes — simply reply to this e-mail or message us on WhatsApp / Telegram.

""" + EN_SIGNATURE)

TEMPLATES: Dict[str, Tuple[str, str]] = {
    "forwarder_ru": FORWARDER_RU,
    "forwarder_en": FORWARDER_EN,
    "carrier_ru": CARRIER_RU,
    "carrier_en": CARRIER_EN,
    "followup_ru": FOLLOWUP_RU,
    "followup_en": FOLLOWUP_EN,
}
