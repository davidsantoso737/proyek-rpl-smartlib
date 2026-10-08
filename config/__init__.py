import pymysql

# PyMySQL pure-python agar runserver jalan di Windows/Laragon tanpa
# wheel mysqlclient yang diblokir Application Control.
# Spoof versi 2.2.1 agar lolos cek Django (>=2.2.1).
pymysql.version_info = (2, 2, 1, "final", 0)
pymysql.install_as_MySQLdb()
