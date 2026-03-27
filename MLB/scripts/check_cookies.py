import sqlite3
db = r'C:\Users\halek\.pp_browser_profile\Default\Network\Cookies'
con = sqlite3.connect(db)
rows = con.execute("SELECT name FROM cookies WHERE host_key LIKE '%prizepicks%'").fetchall()
con.close()
print('PrizePicks cookies:', [r[0] for r in rows])
