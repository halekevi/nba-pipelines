"""
NCAA men's basketball tournament + AP Top 25 metadata used by CBB ranking and combined tickets.
Edit here to add teams (e.g. OKLA/OU aliases) when bracket data is extended.
"""

# Keys follow abbreviations used in CBB / PrizePicks files.
CBB_TOURNEY_2026 = {
    "DUKE": (1, "East"), "CONN": (2, "East"), "MSU": (3, "East"), "KU": (4, "East"),
    "SJU": (5, "East"), "LOU": (6, "East"), "UCLA": (7, "East"), "OSU": (8, "East"),
    "TCU": (9, "East"), "UCF": (10, "East"), "USF": (11, "East"), "UNI": (12, "East"),
    "CBU": (13, "East"), "NDSU": (14, "East"), "FUR": (15, "East"), "SIEN": (16, "East"),
    "ARIZ": (1, "West"), "PUR": (2, "West"), "GONZ": (3, "West"), "ARK": (4, "West"),
    "WIS": (5, "West"), "BYU": (6, "West"), "MIA": (7, "West"), "VILL": (8, "West"),
    "UST": (9, "West"), "MIZZ": (10, "West"), "TEX": (11, "West"), "NCSU": (11, "West"),
    "HP": (12, "West"), "HAW": (13, "West"), "KSU": (14, "West"), "QUC": (15, "West"),
    "LIU": (16, "West"),
    "MICH": (1, "Midwest"), "ISU": (2, "Midwest"), "UVA": (3, "Midwest"), "ALA": (4, "Midwest"),
    "TTU": (5, "Midwest"), "TENN": (6, "Midwest"), "UK": (7, "Midwest"), "UGA": (8, "Midwest"),
    "SLU": (9, "Midwest"), "SCU": (10, "Midwest"), "M-OH": (11, "Midwest"), "SMU": (11, "Midwest"),
    "AKR": (12, "Midwest"), "HOF": (13, "Midwest"), "WRST": (14, "Midwest"), "TNST": (15, "Midwest"),
    "HOW": (16, "Midwest"), "UMBC": (16, "Midwest"),
    "FLA": (1, "South"), "HOU": (2, "South"), "ILL": (3, "South"), "NEB": (4, "South"),
    "VAN": (5, "South"), "UNC": (6, "South"), "SMC": (7, "South"), "CLEM": (8, "South"),
    "IOWA": (9, "South"), "TA&M": (10, "South"), "VCU": (11, "South"), "MCN": (12, "South"),
    "TROY": (13, "South"), "PENN": (14, "South"), "IDA": (15, "South"),
    "PV": (16, "South"), "LEH": (16, "South"),
}

CBB_AP_TOP25_2026 = {
    "DUKE": 1, "ARIZ": 2, "MICH": 3, "FLA": 4, "HOU": 5, "ISU": 6, "CONN": 7,
    "PUR": 8, "UVA": 9, "SJU": 10, "MSU": 11, "GONZ": 12, "ILL": 13, "ARK": 14,
    "NEB": 15, "VAN": 16, "KU": 17, "ALA": 18, "WIS": 19, "TTU": 20, "UNC": 21,
    "SMC": 22, "LOU": 23, "MIA": 23, "TENN": 25,
}
