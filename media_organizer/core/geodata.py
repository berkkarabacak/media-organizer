"""Offline geo lookup: map GPS coordinates to the nearest known place.

Bundles a compact list of world cities (capitals + major regional cities).
Pure Python, no Qt, no network. Distances use the haversine formula.
"""

from __future__ import annotations

import math
from typing import NamedTuple, Optional

EARTH_RADIUS_KM = 6371.0

#: Furthest distance (km) at which a city is still considered "the place".
#: Photos taken further away from every known city map to _unknown-location.
DEFAULT_MAX_KM = 250.0


class City(NamedTuple):
    name: str
    country: str
    lat: float
    lon: float

    @property
    def label(self) -> str:
        return f"{self.name}, {self.country}"


# name, country, latitude, longitude  (~one entry per line, sorted by region)
_CITY_ROWS = """
# --- Europe ---
Tirana|Albania|41.33|19.82
Andorra la Vella|Andorra|42.51|1.52
Vienna|Austria|48.21|16.37
Graz|Austria|47.07|15.44
Salzburg|Austria|47.80|13.04
Innsbruck|Austria|47.27|11.40
Minsk|Belarus|53.90|27.56
Brussels|Belgium|50.85|4.35
Antwerp|Belgium|51.22|4.40
Bruges|Belgium|51.21|3.22
Ghent|Belgium|51.05|3.72
Sarajevo|Bosnia and Herzegovina|43.86|18.41
Sofia|Bulgaria|42.70|23.32
Plovdiv|Bulgaria|42.14|24.75
Varna|Bulgaria|43.21|27.91
Zagreb|Croatia|45.82|15.98
Split|Croatia|43.51|16.44
Dubrovnik|Croatia|42.65|18.09
Nicosia|Cyprus|35.19|33.38
Limassol|Cyprus|34.71|33.02
Prague|Czechia|50.08|14.44
Brno|Czechia|49.20|16.61
Copenhagen|Denmark|55.68|12.57
Aarhus|Denmark|56.16|10.20
Odense|Denmark|55.40|10.39
Tallinn|Estonia|59.44|24.75
Helsinki|Finland|60.17|24.94
Tampere|Finland|61.50|23.76
Turku|Finland|60.45|22.27
Oulu|Finland|65.01|25.47
Paris|France|48.86|2.35
Marseille|France|43.30|5.37
Lyon|France|45.76|4.83
Toulouse|France|43.60|1.44
Nice|France|43.71|7.27
Nantes|France|47.22|-1.55
Strasbourg|France|48.58|7.75
Bordeaux|France|44.84|-0.58
Lille|France|50.63|3.06
Montpellier|France|43.61|3.88
Cannes|France|43.55|7.01
Berlin|Germany|52.52|13.41
Munich|Germany|48.14|11.58
Hamburg|Germany|53.55|9.99
Cologne|Germany|50.94|6.96
Frankfurt|Germany|50.11|8.68
Stuttgart|Germany|48.78|9.18
Dusseldorf|Germany|51.23|6.78
Leipzig|Germany|51.34|12.37
Dresden|Germany|51.05|13.74
Nuremberg|Germany|49.45|11.08
Hanover|Germany|52.38|9.73
Bremen|Germany|53.08|8.81
Dortmund|Germany|51.51|7.47
Essen|Germany|51.46|7.01
Heidelberg|Germany|49.40|8.67
Athens|Greece|37.98|23.73
Thessaloniki|Greece|40.64|22.94
Heraklion|Greece|35.34|25.13
Patras|Greece|38.25|21.74
Rhodes|Greece|36.43|28.22
Budapest|Hungary|47.50|19.04
Debrecen|Hungary|47.53|21.63
Reykjavik|Iceland|64.15|-21.94
Dublin|Ireland|53.35|-6.26
Cork|Ireland|51.90|-8.47
Galway|Ireland|53.27|-9.05
Rome|Italy|41.90|12.50
Milan|Italy|45.46|9.19
Naples|Italy|40.85|14.27
Turin|Italy|45.07|7.69
Florence|Italy|43.77|11.25
Venice|Italy|45.44|12.32
Bologna|Italy|44.49|11.34
Genoa|Italy|44.41|8.93
Palermo|Italy|38.12|13.36
Bari|Italy|41.12|16.87
Catania|Italy|37.50|15.09
Verona|Italy|45.44|10.99
Pisa|Italy|43.72|10.40
Siena|Italy|43.32|11.33
Pristina|Kosovo|42.66|21.17
Riga|Latvia|56.95|24.11
Vaduz|Liechtenstein|47.14|9.52
Vilnius|Lithuania|54.69|25.28
Kaunas|Lithuania|54.90|23.90
Luxembourg City|Luxembourg|49.61|6.13
Valletta|Malta|35.90|14.51
Chisinau|Moldova|47.01|28.86
Monaco|Monaco|43.74|7.42
Podgorica|Montenegro|42.43|19.26
Amsterdam|Netherlands|52.37|4.90
Rotterdam|Netherlands|51.92|4.48
The Hague|Netherlands|52.08|4.30
Utrecht|Netherlands|52.09|5.12
Eindhoven|Netherlands|51.44|5.47
Skopje|North Macedonia|42.00|21.43
Oslo|Norway|59.91|10.75
Bergen|Norway|60.39|5.32
Trondheim|Norway|63.43|10.40
Tromso|Norway|69.65|18.96
Warsaw|Poland|52.23|21.01
Krakow|Poland|50.06|19.94
Gdansk|Poland|54.35|18.65
Wroclaw|Poland|51.11|17.04
Poznan|Poland|52.41|16.93
Lodz|Poland|51.77|19.46
Zakopane|Poland|49.30|19.95
Lisbon|Portugal|38.72|-9.14
Porto|Portugal|41.15|-8.61
Faro|Portugal|37.02|-7.94
Coimbra|Portugal|40.20|-8.41
Funchal|Portugal|32.67|-16.92
Bucharest|Romania|44.43|26.10
Cluj-Napoca|Romania|46.77|23.59
Timisoara|Romania|45.75|21.23
Brasov|Romania|45.66|25.60
Moscow|Russia|55.76|37.62
Saint Petersburg|Russia|59.93|30.34
Novosibirsk|Russia|55.03|82.92
Yekaterinburg|Russia|56.84|60.65
Kazan|Russia|55.79|49.12
Nizhny Novgorod|Russia|56.30|44.00
Samara|Russia|53.20|50.15
Sochi|Russia|43.59|39.73
Vladivostok|Russia|43.12|131.89
Kaliningrad|Russia|54.71|20.51
Irkutsk|Russia|52.29|104.30
San Marino|San Marino|43.94|12.45
Belgrade|Serbia|44.79|20.45
Novi Sad|Serbia|45.27|19.83
Bratislava|Slovakia|48.15|17.11
Kosice|Slovakia|48.72|21.26
Ljubljana|Slovenia|46.06|14.51
Maribor|Slovenia|46.55|15.65
Madrid|Spain|40.42|-3.70
Barcelona|Spain|41.39|2.17
Valencia|Spain|39.47|-0.38
Seville|Spain|37.39|-5.98
Bilbao|Spain|43.26|-2.93
Malaga|Spain|36.72|-4.42
Zaragoza|Spain|41.65|-0.89
Granada|Spain|37.18|-3.60
Palma|Spain|39.57|2.65
Alicante|Spain|38.35|-0.48
Cordoba|Spain|37.89|-4.78
San Sebastian|Spain|43.32|-1.98
Santiago de Compostela|Spain|42.88|-8.54
Ibiza|Spain|38.91|1.42
Stockholm|Sweden|59.33|18.07
Gothenburg|Sweden|57.71|11.97
Malmo|Sweden|55.60|13.00
Uppsala|Sweden|59.86|17.64
Bern|Switzerland|46.95|7.45
Zurich|Switzerland|47.37|8.54
Geneva|Switzerland|46.20|6.14
Basel|Switzerland|47.56|7.59
Lausanne|Switzerland|46.52|6.63
Lucerne|Switzerland|47.05|8.31
Interlaken|Switzerland|46.69|7.86
Kyiv|Ukraine|50.45|30.52
Kharkiv|Ukraine|49.99|36.23
Odesa|Ukraine|46.48|30.72
Lviv|Ukraine|49.84|24.03
Dnipro|Ukraine|48.47|35.04
London|United Kingdom|51.51|-0.13
Manchester|United Kingdom|53.48|-2.24
Birmingham|United Kingdom|52.48|-1.90
Edinburgh|United Kingdom|55.95|-3.19
Glasgow|United Kingdom|55.86|-4.25
Liverpool|United Kingdom|53.41|-2.99
Bristol|United Kingdom|51.45|-2.59
Leeds|United Kingdom|53.80|-1.55
Cardiff|United Kingdom|51.48|-3.18
Belfast|United Kingdom|54.60|-5.93
Newcastle|United Kingdom|54.98|-1.62
Oxford|United Kingdom|51.75|-1.25
Cambridge|United Kingdom|52.21|0.12
York|United Kingdom|53.96|-1.08
Bath|United Kingdom|51.38|-2.36
Brighton|United Kingdom|50.82|-0.14
Inverness|United Kingdom|57.48|-4.22
Vatican City|Vatican City|41.90|12.45
# --- Asia ---
Yerevan|Armenia|40.18|44.51
Baku|Azerbaijan|40.41|49.87
Manama|Bahrain|26.23|50.58
Dhaka|Bangladesh|23.81|90.41
Chittagong|Bangladesh|22.36|91.78
Thimphu|Bhutan|27.47|89.64
Bandar Seri Begawan|Brunei|4.94|114.95
Phnom Penh|Cambodia|11.56|104.93
Siem Reap|Cambodia|13.36|103.86
Beijing|China|39.90|116.40
Shanghai|China|31.23|121.47
Guangzhou|China|23.13|113.26
Shenzhen|China|22.54|114.06
Chengdu|China|30.57|104.07
Chongqing|China|29.56|106.55
Xian|China|34.34|108.94
Hangzhou|China|30.27|120.16
Wuhan|China|30.59|114.31
Nanjing|China|32.06|118.80
Tianjin|China|39.34|117.36
Suzhou|China|31.30|120.58
Qingdao|China|36.07|120.38
Dalian|China|38.91|121.61
Shenyang|China|41.81|123.43
Kunming|China|25.04|102.71
Guilin|China|25.27|110.29
Lhasa|China|29.65|91.14
Urumqi|China|43.83|87.62
Harbin|China|45.80|126.53
Hong Kong|Hong Kong|22.32|114.17
Macau|Macau|22.20|113.55
Tbilisi|Georgia|41.72|44.79
New Delhi|India|28.61|77.21
Mumbai|India|19.08|72.88
Bangalore|India|12.97|77.59
Chennai|India|13.08|80.27
Kolkata|India|22.57|88.36
Hyderabad|India|17.38|78.49
Jaipur|India|26.91|75.79
Goa|India|15.30|74.12
Kochi|India|9.93|76.27
Agra|India|27.18|78.01
Varanasi|India|25.32|82.99
Udaipur|India|24.58|73.71
Amritsar|India|31.63|74.87
Shimla|India|31.10|77.17
Leh|India|34.16|77.58
Jakarta|Indonesia|-6.21|106.85
Surabaya|Indonesia|-7.25|112.75
Bandung|Indonesia|-6.92|107.61
Denpasar|Indonesia|-8.65|115.22
Yogyakarta|Indonesia|-7.80|110.36
Medan|Indonesia|3.59|98.67
Ubud|Indonesia|-8.51|115.26
Tehran|Iran|35.69|51.39
Isfahan|Iran|32.65|51.68
Shiraz|Iran|29.60|52.53
Mashhad|Iran|36.30|59.61
Baghdad|Iraq|33.31|44.36
Erbil|Iraq|36.19|44.01
Jerusalem|Israel|31.77|35.21
Tel Aviv|Israel|32.08|34.78
Haifa|Israel|32.79|34.99
Eilat|Israel|29.56|34.95
Tokyo|Japan|35.68|139.69
Osaka|Japan|34.69|135.50
Kyoto|Japan|35.01|135.77
Nagoya|Japan|35.18|136.91
Sapporo|Japan|43.06|141.35
Fukuoka|Japan|33.59|130.40
Hiroshima|Japan|34.39|132.46
Nara|Japan|34.69|135.80
Okinawa|Japan|26.21|127.68
Amman|Jordan|31.95|35.93
Petra|Jordan|30.32|35.44
Aqaba|Jordan|29.53|35.01
Almaty|Kazakhstan|43.24|76.89
Astana|Kazakhstan|51.16|71.43
Kuwait City|Kuwait|29.38|47.99
Bishkek|Kyrgyzstan|42.87|74.59
Vientiane|Laos|17.97|102.63
Luang Prabang|Laos|19.88|102.13
Beirut|Lebanon|33.89|35.50
Kuala Lumpur|Malaysia|3.14|101.69
Penang|Malaysia|5.42|100.33
Johor Bahru|Malaysia|1.49|103.74
Kota Kinabalu|Malaysia|5.98|116.07
Langkawi|Malaysia|6.35|99.80
Male|Maldives|4.18|73.51
Ulaanbaatar|Mongolia|47.89|106.91
Yangon|Myanmar|16.84|96.17
Mandalay|Myanmar|21.96|96.08
Bagan|Myanmar|21.17|94.86
Kathmandu|Nepal|27.71|85.32
Pokhara|Nepal|28.21|83.99
Pyongyang|North Korea|39.03|125.75
Muscat|Oman|23.59|58.41
Salalah|Oman|17.02|54.09
Islamabad|Pakistan|33.68|73.04
Karachi|Pakistan|24.86|67.00
Lahore|Pakistan|31.55|74.34
Jericho|Palestine|31.86|35.46
Manila|Philippines|14.60|120.98
Cebu|Philippines|10.32|123.91
Davao|Philippines|7.07|125.61
Boracay|Philippines|11.97|121.92
Palawan|Philippines|9.83|118.74
Doha|Qatar|25.29|51.53
Riyadh|Saudi Arabia|24.71|46.68
Jeddah|Saudi Arabia|21.49|39.19
Mecca|Saudi Arabia|21.42|39.83
Medina|Saudi Arabia|24.47|39.61
Singapore|Singapore|1.35|103.82
Seoul|South Korea|37.57|126.98
Busan|South Korea|35.18|129.08
Incheon|South Korea|37.46|126.71
Jeju|South Korea|33.50|126.53
Colombo|Sri Lanka|6.93|79.85
Kandy|Sri Lanka|7.29|80.64
Galle|Sri Lanka|6.03|80.22
Damascus|Syria|33.51|36.29
Taipei|Taiwan|25.03|121.57
Kaohsiung|Taiwan|22.63|120.30
Taichung|Taiwan|24.15|120.68
Dushanbe|Tajikistan|38.56|68.79
Bangkok|Thailand|13.76|100.50
Chiang Mai|Thailand|18.79|98.99
Phuket|Thailand|7.88|98.39
Krabi|Thailand|8.09|98.91
Pattaya|Thailand|12.92|100.88
Koh Samui|Thailand|9.51|100.00
Ayutthaya|Thailand|14.35|100.57
Dili|Timor-Leste|-8.56|125.57
Istanbul|Turkey|41.01|28.98
Ankara|Turkey|39.93|32.86
Izmir|Turkey|38.42|27.14
Antalya|Turkey|36.90|30.71
Cappadocia|Turkey|38.64|34.83
Bodrum|Turkey|37.03|27.43
Pamukkale|Turkey|37.92|29.12
Turkmenabat|Turkmenistan|39.07|63.58
Ashgabat|Turkmenistan|37.96|58.38
Dubai|United Arab Emirates|25.20|55.27
Abu Dhabi|United Arab Emirates|24.45|54.38
Sharjah|United Arab Emirates|25.35|55.42
Tashkent|Uzbekistan|41.30|69.24
Samarkand|Uzbekistan|39.66|66.96
Bukhara|Uzbekistan|39.77|64.42
Hanoi|Vietnam|21.03|105.85
Ho Chi Minh City|Vietnam|10.82|106.63
Da Nang|Vietnam|16.05|108.21
Hoi An|Vietnam|15.88|108.33
Hue|Vietnam|16.46|107.59
Nha Trang|Vietnam|12.24|109.19
Ha Long|Vietnam|20.97|107.09
Sapa|Vietnam|22.34|103.84
Sanaa|Yemen|15.35|44.21
# --- Africa ---
Algiers|Algeria|36.75|3.06
Oran|Algeria|35.70|-0.64
Luanda|Angola|-8.84|13.23
Porto-Novo|Benin|6.50|2.63
Gaborone|Botswana|-24.63|25.91
Maun|Botswana|-19.98|23.42
Ouagadougou|Burkina Faso|12.37|-1.52
Gitega|Burundi|-3.43|29.93
Yaounde|Cameroon|3.85|11.50
Douala|Cameroon|4.05|9.70
Praia|Cabo Verde|14.92|-23.51
Bangui|Central African Republic|4.39|18.56
N'Djamena|Chad|12.13|15.06
Moroni|Comoros|-11.70|43.26
Brazzaville|Congo|-4.26|15.24
Kinshasa|DR Congo|-4.44|15.27
Djibouti City|Djibouti|11.59|43.15
Cairo|Egypt|30.04|31.24
Alexandria|Egypt|31.20|29.92
Luxor|Egypt|25.69|32.64
Aswan|Egypt|24.09|32.90
Sharm El Sheikh|Egypt|27.92|34.33
Hurghada|Egypt|27.26|33.81
Malabo|Equatorial Guinea|3.75|8.78
Asmara|Eritrea|15.32|38.93
Mbabane|Eswatini|-26.32|31.13
Addis Ababa|Ethiopia|9.02|38.75
Libreville|Gabon|0.39|9.45
Banjul|Gambia|13.45|-16.58
Accra|Ghana|5.60|-0.19
Kumasi|Ghana|6.69|-1.62
Conakry|Guinea|9.64|-13.58
Bissau|Guinea-Bissau|11.86|-15.60
Abidjan|Ivory Coast|5.35|-4.01
Yamoussoukro|Ivory Coast|6.82|-5.28
Nairobi|Kenya|-1.29|36.82
Mombasa|Kenya|-4.05|39.67
Maseru|Lesotho|-29.31|27.48
Monrovia|Liberia|6.30|-10.80
Tripoli|Libya|32.89|13.19
Antananarivo|Madagascar|-18.91|47.54
Lilongwe|Malawi|-13.96|33.77
Zomba|Malawi|-15.39|35.32
Bamako|Mali|12.64|-8.00
Nouakchott|Mauritania|18.09|-15.95
Port Louis|Mauritius|-20.16|57.50
Rabat|Morocco|34.02|-6.84
Casablanca|Morocco|33.57|-7.59
Marrakech|Morocco|31.63|-7.99
Fez|Morocco|34.02|-5.01
Tangier|Morocco|35.78|-5.81
Chefchaouen|Morocco|35.17|-5.27
Agadir|Morocco|30.43|-9.60
Maputo|Mozambique|-25.97|32.58
Windhoek|Namibia|-22.56|17.08
Swakopmund|Namibia|-22.68|14.53
Niamey|Niger|13.51|2.11
Abuja|Nigeria|9.06|7.50
Lagos|Nigeria|6.52|3.38
Kano|Nigeria|12.00|8.52
Kigali|Rwanda|-1.94|30.06
Sao Tome|Sao Tome and Principe|0.34|6.73
Dakar|Senegal|14.72|-17.47
Victoria|Seychelles|-4.62|55.45
Freetown|Sierra Leone|8.48|-13.23
Mogadishu|Somalia|2.05|45.32
Pretoria|South Africa|-25.75|28.19
Cape Town|South Africa|-33.92|18.42
Johannesburg|South Africa|-26.20|28.05
Durban|South Africa|-29.86|31.02
Kruger National Park|South Africa|-23.99|31.55
Juba|South Sudan|4.86|31.57
Khartoum|Sudan|15.50|32.53
Dodoma|Tanzania|-6.16|35.75
Dar es Salaam|Tanzania|-6.79|39.21
Zanzibar City|Tanzania|-6.16|39.19
Arusha|Tanzania|-3.39|36.68
Lome|Togo|6.17|1.23
Tunis|Tunisia|36.81|10.18
Sousse|Tunisia|35.83|10.64
Kampala|Uganda|0.35|32.58
Entebbe|Uganda|0.06|32.44
Lusaka|Zambia|-15.41|28.28
Livingstone|Zambia|-17.84|25.86
Harare|Zimbabwe|-17.83|31.05
Victoria Falls|Zimbabwe|-17.93|25.83
# --- North America ---
Nassau|Bahamas|25.04|-77.36
Bridgetown|Barbados|13.10|-59.61
Belmopan|Belize|17.25|-88.77
Belize City|Belize|17.50|-88.20
Ottawa|Canada|45.42|-75.70
Toronto|Canada|43.65|-79.38
Montreal|Canada|45.50|-73.57
Vancouver|Canada|49.28|-123.12
Calgary|Canada|51.05|-114.07
Quebec City|Canada|46.81|-71.21
Edmonton|Canada|53.55|-113.49
Winnipeg|Canada|49.90|-97.14
Halifax|Canada|44.65|-63.58
Victoria|Canada|48.43|-123.37
Banff|Canada|51.18|-115.57
Whistler|Canada|50.12|-122.95
Whitehorse|Canada|60.72|-135.05
Yellowknife|Canada|62.45|-114.37
San Jose|Costa Rica|9.93|-84.08
La Fortuna|Costa Rica|10.47|-84.64
Havana|Cuba|23.11|-82.37
Varadero|Cuba|23.14|-81.29
Willemstad|Curacao|12.12|-68.94
Santo Domingo|Dominican Republic|18.49|-69.93
Punta Cana|Dominican Republic|18.56|-68.37
San Salvador|El Salvador|13.69|-89.22
Guatemala City|Guatemala|14.63|-90.51
Antigua Guatemala|Guatemala|14.56|-90.73
Port-au-Prince|Haiti|18.59|-72.31
Tegucigalpa|Honduras|14.07|-87.19
Roatan|Honduras|16.32|-86.54
Kingston|Jamaica|17.97|-76.79
Montego Bay|Jamaica|18.47|-77.92
Mexico City|Mexico|19.43|-99.13
Cancun|Mexico|21.16|-86.85
Guadalajara|Mexico|20.67|-103.35
Monterrey|Mexico|25.69|-100.32
Playa del Carmen|Mexico|20.63|-87.07
Tulum|Mexico|20.21|-87.43
Puerto Vallarta|Mexico|20.65|-105.22
Oaxaca|Mexico|17.07|-96.73
San Miguel de Allende|Mexico|20.91|-100.74
Managua|Nicaragua|12.11|-86.24
Granada Nicaragua|Nicaragua|11.93|-85.96
Panama City|Panama|8.98|-79.52
San Juan|Puerto Rico|18.47|-66.11
Port of Spain|Trinidad and Tobago|10.65|-61.50
Washington DC|United States|38.91|-77.04
New York City|United States|40.71|-74.01
Los Angeles|United States|34.05|-118.24
Chicago|United States|41.88|-87.63
San Francisco|United States|37.77|-122.42
Miami|United States|25.76|-80.19
Las Vegas|United States|36.17|-115.14
Boston|United States|42.36|-71.06
Seattle|United States|47.61|-122.33
New Orleans|United States|29.95|-90.07
Orlando|United States|28.54|-81.38
San Diego|United States|32.72|-117.16
Denver|United States|39.74|-104.99
Austin|United States|30.27|-97.74
Houston|United States|29.76|-95.37
Dallas|United States|32.78|-96.80
Phoenix|United States|33.45|-112.07
Philadelphia|United States|39.95|-75.17
Atlanta|United States|33.75|-84.39
Nashville|United States|36.16|-86.78
Portland|United States|45.52|-122.68
Salt Lake City|United States|40.76|-111.89
Honolulu|United States|21.31|-157.86
Anchorage|United States|61.22|-149.90
Minneapolis|United States|44.98|-93.27
Detroit|United States|42.33|-83.05
St Louis|United States|38.63|-90.20
Kansas City|United States|39.10|-94.58
Tampa|United States|27.95|-82.46
Savannah|United States|32.08|-81.09
Charleston|United States|32.78|-79.93
Santa Fe|United States|35.69|-105.94
Sedona|United States|34.87|-111.76
Grand Canyon Village|United States|36.05|-112.14
Yellowstone|United States|44.43|-110.59
Yosemite|United States|37.75|-119.59
Juneau|United States|58.30|-134.42
Key West|United States|24.56|-81.78
Aspen|United States|39.19|-106.82
# --- South America ---
Buenos Aires|Argentina|-34.60|-58.38
Cordoba Argentina|Argentina|-31.42|-64.18
Mendoza|Argentina|-32.89|-68.84
Bariloche|Argentina|-41.13|-71.31
Ushuaia|Argentina|-54.80|-68.30
Salta|Argentina|-24.79|-65.41
Sucre|Bolivia|-19.02|-65.26
La Paz|Bolivia|-16.50|-68.15
Santa Cruz Bolivia|Bolivia|-17.79|-63.18
Uyuni|Bolivia|-20.46|-66.83
Brasilia|Brazil|-15.79|-47.88
Rio de Janeiro|Brazil|-22.91|-43.17
Sao Paulo|Brazil|-23.55|-46.63
Salvador Brazil|Brazil|-12.97|-38.50
Florianopolis|Brazil|-27.60|-48.55
Manaus|Brazil|-3.12|-60.02
Recife|Brazil|-8.05|-34.90
Fortaleza|Brazil|-3.73|-38.52
Belo Horizonte|Brazil|-19.92|-43.94
Curitiba|Brazil|-25.43|-49.27
Porto Alegre|Brazil|-30.03|-51.23
Foz do Iguacu|Brazil|-25.52|-54.59
Santiago|Chile|-33.45|-70.67
Valparaiso|Chile|-33.05|-71.62
San Pedro de Atacama|Chile|-22.91|-68.20
Puerto Natales|Chile|-51.73|-72.51
Punta Arenas|Chile|-53.16|-70.92
Bogota|Colombia|4.71|-74.07
Medellin|Colombia|6.25|-75.56
Cartagena|Colombia|10.39|-75.48
Cali|Colombia|3.44|-76.52
Santa Marta|Colombia|11.24|-74.20
Quito|Ecuador|-0.18|-78.47
Guayaquil|Ecuador|-2.17|-79.90
Cuenca Ecuador|Ecuador|-2.90|-79.00
Galapagos|Ecuador|-0.74|-90.33
Georgetown|Guyana|6.80|-58.16
Asuncion|Paraguay|-25.26|-57.58
Lima|Peru|-12.05|-77.04
Cusco|Peru|-13.53|-71.97
Arequipa|Peru|-16.41|-71.54
Machu Picchu|Peru|-13.16|-72.54
Iquitos|Peru|-3.75|-73.25
Paramaribo|Suriname|5.85|-55.20
Montevideo|Uruguay|-34.90|-56.16
Punta del Este|Uruguay|-34.97|-54.95
Caracas|Venezuela|10.49|-66.88
# --- Oceania ---
Canberra|Australia|-35.28|149.13
Sydney|Australia|-33.87|151.21
Melbourne|Australia|-37.81|144.96
Brisbane|Australia|-27.47|153.03
Perth|Australia|-31.95|115.86
Adelaide|Australia|-34.93|138.60
Hobart|Australia|-42.88|147.33
Darwin|Australia|-12.46|130.84
Cairns|Australia|-16.92|145.77
Gold Coast|Australia|-28.02|153.43
Uluru|Australia|-25.34|131.04
Alice Springs|Australia|-23.70|133.88
Suva|Fiji|-18.14|178.44
Auckland|New Zealand|-36.85|174.76
Wellington|New Zealand|-41.29|174.78
Christchurch|New Zealand|-43.53|172.64
Queenstown|New Zealand|-45.03|168.66
Rotorua|New Zealand|-38.14|176.25
Dunedin|New Zealand|-45.87|170.50
Port Moresby|Papua New Guinea|-9.44|147.18
Apia|Samoa|-13.83|-171.75
Nuku'alofa|Tonga|-21.14|-175.20
Port Vila|Vanuatu|-17.73|168.32
Papeete|French Polynesia|-17.54|-149.57
Noumea|New Caledonia|-22.27|166.45
"""

CITIES: list[City] = []
for _line in _CITY_ROWS.strip().splitlines():
    _line = _line.strip()
    if not _line or _line.startswith("#"):
        continue
    _name, _country, _lat, _lon = _line.split("|")
    CITIES.append(City(_name, _country, float(_lat), float(_lon)))
del _line, _name, _country, _lat, _lon


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def nearest_city(lat: float, lon: float, max_km: float = DEFAULT_MAX_KM) -> Optional[City]:
    """Return the closest known city to (lat, lon), or None when every known
    city is further than `max_km` away (open ocean, deep countryside...)."""
    best: Optional[City] = None
    best_km = max_km
    for city in CITIES:
        d = haversine_km(lat, lon, city.lat, city.lon)
        if d < best_km:
            best, best_km = city, d
    return best


def location_label(lat: float, lon: float, max_km: float = DEFAULT_MAX_KM) -> Optional[str]:
    """Folder-friendly label like 'Istanbul, Turkey', or None if unknown."""
    city = nearest_city(lat, lon, max_km)
    return city.label if city else None
