from django.db import connections, transaction


def get_menus():
    with connections["replica"].cursor() as cursor:
        cursor.execute("""
            SELECT
                   d.menu_id,
                   d.tarih,
                   d.yemekhane_id,
                   d.yemekhane,
                   d.ogun_id,
                   d.ogun,
                   d.ogun_sira,
                   d.rezervasyon_son,
                   d.ogrenci_fiyati,
                   d.personel_fiyati,
                   f.kapasite,
                   f.rezerve,
                   f.bos_yer,
                   f.doluluk_yuzde,
                   d.yemek_id,
                   d.yemek,
                   d.kategori,
                   d.kalori,
                   d.vejetaryen,
                   d.alerjenler
            FROM v_menu_detay AS d
            JOIN v_menu_doluluk AS f ON f.menu_id = d.menu_id
            ORDER BY d.tarih, d.yemekhane_id, d.ogun_sira, d.kategori_sira, d.yemek
        """)
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    menus = {}
    for row in rows:
        menu_id = row['menu_id']
        if menu_id not in menus:
            menus[menu_id] = {
                key: row[key]
                for key in (
                    'menu_id', 'tarih', 'yemekhane_id', 'yemekhane', 'ogun_id',
                    'ogun', 'rezervasyon_son', 'ogrenci_fiyati', 'personel_fiyati',
                    'kapasite', 'rezerve', 'bos_yer', 'doluluk_yuzde',
                )
            }
            menus[menu_id]['yemekler'] = []
            menus[menu_id]['toplam_kalori'] = 0

        menus[menu_id]['yemekler'].append({
            'ad': row['yemek'],
            'kategori': row['kategori'],
            'kalori': row['kalori'],
            'vejetaryen': bool(row['vejetaryen']),
            'alerjenler': row['alerjenler'] or '',
        })
        menus[menu_id]['toplam_kalori'] += row['kalori']

    return list(menus.values())


def find_active_user_by_email(email):
    """E-posta ile aktif kullanıcıyı salt okunur replikada arar."""
    with connections['replica'].cursor() as cursor:
        cursor.execute(
            """
            SELECT id, ad, soyad, eposta, sifre_hash, rol, bakiye
            FROM kullanicilar
            WHERE eposta = %s AND aktif = 1
            LIMIT 1
            """,
            [email],
        )
        row = cursor.fetchone()

    if row is None:
        return None

    return {
        'id': row[0],
        'ad': row[1],
        'soyad': row[2],
        'eposta': row[3],
        'sifre_hash': row[4],
        'rol': row[5],
        'bakiye': str(row[6]),
    }


def find_active_user_by_id(user_id):
    """Oturum açmış aktif kullanıcıyı salt okunur replikada bulur."""
    with connections['replica'].cursor() as cursor:
        cursor.execute(
            """
            SELECT id, ad, soyad, eposta, rol, bakiye
            FROM kullanicilar
            WHERE id = %s AND aktif = 1
            LIMIT 1
            """,
            [user_id],
        )
        row = cursor.fetchone()

    if row is None:
        return None

    return {
        'id': row[0],
        'ad': row[1],
        'soyad': row[2],
        'eposta': row[3],
        'rol': row[4],
        'bakiye': str(row[5]),
    }


def make_reservation(user_id, menu_id):
    """Rezervasyonu kaynak veritabanındaki yordamla yapar."""
    with connections['default'].cursor() as cursor:
        cursor.execute('CALL sp_rezervasyon_yap(%s, %s)', [user_id, menu_id])
        sonuc = cursor.fetchone()
        # MySQL CALL sonrasında kalan sonuç kümelerini temizle.
        while cursor.nextset():
            cursor.fetchall()

        # İşlemden hemen sonraki bakiye kaynaktan okunur; replika gecikebilir.
        cursor.execute(
            'SELECT bakiye FROM kullanicilar WHERE id = %s',
            [user_id],
        )
        bakiye = cursor.fetchone()[0]

    return {
        'rezervasyon_id': sonuc[0],
        'ucret': str(sonuc[1]),
        'bakiye': str(bakiye),
    }


def get_user_reservations(user_id):
    """Kullanıcının rezervasyonlarını salt okunur replikadan getirir."""
    with connections['replica'].cursor() as cursor:
        cursor.execute(
            """
            SELECT r.id AS rezervasyon_id, r.menu_id, r.durum, r.ucret,
                   m.tarih, yh.ad AS yemekhane, o.ad AS ogun,
                   TIMESTAMP(m.tarih, o.son_saat) AS rezervasyon_son
            FROM rezervasyonlar r
            JOIN menuler m ON m.id = r.menu_id
            JOIN yemekhaneler yh ON yh.id = m.yemekhane_id
            JOIN ogunler o ON o.id = m.ogun_id
            WHERE r.kullanici_id = %s
            ORDER BY m.tarih DESC, o.sira DESC, r.id DESC
            """,
            [user_id],
        )
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    for row in rows:
        row['ucret'] = str(row['ucret'])
    return rows


def cancel_reservation(user_id, reservation_id):
    """İptali kaynak yordamında yapar, güncel bakiyeyi kaynaktan okur."""
    with connections['default'].cursor() as cursor:
        cursor.execute(
            'CALL sp_rezervasyon_iptal(%s, %s)',
            [user_id, reservation_id],
        )
        sonuc = cursor.fetchone()
        while cursor.nextset():
            cursor.fetchall()

        cursor.execute(
            'SELECT bakiye FROM kullanicilar WHERE id = %s',
            [user_id],
        )
        bakiye = cursor.fetchone()[0]

    return {
        'rezervasyon_id': sonuc[0],
        'iade': str(sonuc[1]),
        'bakiye': str(bakiye),
    }


def get_balance_and_movements(user_id):
    """Bakiye ve hareket geçmişini salt okunur replikadan getirir."""
    with connections['replica'].cursor() as cursor:
        cursor.execute(
            'SELECT bakiye FROM kullanicilar WHERE id = %s',
            [user_id],
        )
        bakiye_row = cursor.fetchone()
        if bakiye_row is None:
            return None

        cursor.execute(
            """
            SELECT id AS hareket_id, tur, tutar, aciklama, olusturma
            FROM bakiye_hareketleri
            WHERE kullanici_id = %s
            ORDER BY olusturma DESC, id DESC
            """,
            [user_id],
        )
        columns = [column[0] for column in cursor.description]
        hareketler = [dict(zip(columns, row)) for row in cursor.fetchall()]

    for hareket in hareketler:
        hareket['tutar'] = str(hareket['tutar'])
    return {'bakiye': str(bakiye_row[0]), 'hareketler': hareketler}


def load_balance(user_id, amount):
    """Bakiye yüklemesini kaynak yordamında yapar ve güncel bakiyeyi kaynaktan okur."""
    with connections['default'].cursor() as cursor:
        cursor.execute(
            'CALL sp_bakiye_yukle(%s, %s)',
            [user_id, amount],
        )
        sonuc = cursor.fetchone()
        while cursor.nextset():
            cursor.fetchall()

        cursor.execute(
            'SELECT bakiye FROM kullanicilar WHERE id = %s',
            [user_id],
        )
        bakiye = cursor.fetchone()[0]

    return {
        'hareket_id': sonuc[0],
        'bakiye': str(bakiye),
    }


def get_reviewable_menus(user_id):
    """Kullanılmış menüleri ve varsa kullanıcının değerlendirmesini replikadan getirir."""
    with connections['replica'].cursor() as cursor:
        cursor.execute(
            """
            SELECT m.id AS menu_id, m.tarih, yh.ad AS yemekhane, o.ad AS ogun,
                   d.puan, d.yorum
            FROM rezervasyonlar r
            JOIN menuler m ON m.id = r.menu_id
            JOIN yemekhaneler yh ON yh.id = m.yemekhane_id
            JOIN ogunler o ON o.id = m.ogun_id
            LEFT JOIN degerlendirmeler d
              ON d.menu_id = m.id AND d.kullanici_id = r.kullanici_id
            WHERE r.kullanici_id = %s AND r.durum = 'kullanildi'
            ORDER BY m.tarih DESC, o.sira DESC
            """,
            [user_id],
        )
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def create_review(user_id, menu_id, rating, comment):
    """Kaynakta, kullanılmış rezervasyon için tek bir değerlendirme kaydeder."""
    with transaction.atomic(using='default'):
        with connections['default'].cursor() as cursor:
            cursor.execute(
                """
                SELECT durum FROM rezervasyonlar
                WHERE kullanici_id = %s AND menu_id = %s
                FOR UPDATE
                """,
                [user_id, menu_id],
            )
            reservation = cursor.fetchone()
            if reservation is None or reservation[0] != 'kullanildi':
                raise ValueError('Yalnızca kullanılmış menüler değerlendirilebilir.')

            cursor.execute(
                'SELECT id FROM degerlendirmeler WHERE kullanici_id = %s AND menu_id = %s',
                [user_id, menu_id],
            )
            if cursor.fetchone() is not None:
                raise ValueError('Bu menüyü zaten değerlendirdiniz.')

            cursor.execute(
                """
                INSERT INTO degerlendirmeler (kullanici_id, menu_id, puan, yorum)
                VALUES (%s, %s, %s, %s)
                """,
                [user_id, menu_id, rating, comment or None],
            )
            review_id = cursor.lastrowid

    return {'degerlendirme_id': review_id, 'menu_id': menu_id, 'puan': rating, 'yorum': comment}


def get_admin_panel_data():
    """Yönetici form seçeneklerini ve doluluk/puan özetlerini replikadan getirir."""
    with connections['replica'].cursor() as cursor:
        cursor.execute('SELECT id, ad FROM yemekhaneler ORDER BY ad')
        yemekhaneler = [{'id': row[0], 'ad': row[1]} for row in cursor.fetchall()]

        cursor.execute('SELECT id, ad FROM ogunler ORDER BY sira')
        ogunler = [{'id': row[0], 'ad': row[1]} for row in cursor.fetchall()]

        cursor.execute(
            """
            SELECT y.id, y.ad, k.ad AS kategori
            FROM yemekler y
            JOIN yemek_kategorileri k ON k.id = y.kategori_id
            ORDER BY k.sira, y.ad
            """
        )
        yemekler = [
            {'id': row[0], 'ad': row[1], 'kategori': row[2]}
            for row in cursor.fetchall()
        ]

        cursor.execute(
            """
            SELECT f.menu_id, m.tarih, f.yemekhane_id, f.yemekhane,
                   f.ogun_id, f.ogun, m.ogrenci_fiyati, m.personel_fiyati,
                   f.kapasite, f.rezerve, f.bos_yer, f.doluluk_yuzde,
                   p.oy_sayisi, p.ortalama_puan,
                   GROUP_CONCAT(my.yemek_id ORDER BY my.yemek_id) AS yemek_idleri
            FROM v_menu_doluluk f
            JOIN menuler m ON m.id = f.menu_id
            LEFT JOIN v_menu_puan p ON p.menu_id = f.menu_id
            LEFT JOIN menu_yemekleri my ON my.menu_id = f.menu_id
            GROUP BY f.menu_id, m.tarih, f.yemekhane_id, f.yemekhane,
                     f.ogun_id, f.ogun, m.ogrenci_fiyati, m.personel_fiyati,
                     f.kapasite, f.rezerve, f.bos_yer, f.doluluk_yuzde,
                     p.oy_sayisi, p.ortalama_puan
            ORDER BY m.tarih DESC, f.yemekhane, f.ogun_id
            """
        )
        columns = [column[0] for column in cursor.description]
        menuler = []
        for row in cursor.fetchall():
            menu = dict(zip(columns, row))
            menu['ogrenci_fiyati'] = str(menu['ogrenci_fiyati'])
            menu['personel_fiyati'] = str(menu['personel_fiyati'])
            menu['yemek_idleri'] = [
                int(item) for item in (menu['yemek_idleri'] or '').split(',') if item
            ]
            menuler.append(menu)

    return {'yemekhaneler': yemekhaneler, 'ogunler': ogunler, 'yemekler': yemekler, 'menuler': menuler}


def save_admin_menu(user_id, menu_id, restaurant_id, menu_date, meal_id, student_price, staff_price, food_ids):
    """Yönetici yetkisini kaynakta doğrulayıp menüyü kaynak veritabanında kaydeder."""
    with transaction.atomic(using='default'):
        with connections['default'].cursor() as cursor:
            cursor.execute(
                'SELECT rol, aktif FROM kullanicilar WHERE id = %s FOR UPDATE',
                [user_id],
            )
            user = cursor.fetchone()
            if user is None or user[0] != 'yonetici' or not user[1]:
                raise PermissionError('Bu işlem yalnızca aktif yöneticiler içindir.')

            if menu_id is None:
                cursor.execute(
                    """
                    INSERT INTO menuler (yemekhane_id, tarih, ogun_id, ogrenci_fiyati, personel_fiyati)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    [restaurant_id, menu_date, meal_id, student_price, staff_price],
                )
                menu_id = cursor.lastrowid
            else:
                cursor.execute(
                    """
                    UPDATE menuler
                    SET yemekhane_id = %s, tarih = %s, ogun_id = %s,
                        ogrenci_fiyati = %s, personel_fiyati = %s
                    WHERE id = %s
                    """,
                    [restaurant_id, menu_date, meal_id, student_price, staff_price, menu_id],
                )
                if cursor.rowcount == 0:
                    cursor.execute('SELECT id FROM menuler WHERE id = %s', [menu_id])
                    if cursor.fetchone() is None:
                        raise ValueError('Düzenlenecek menü bulunamadı.')
                cursor.execute('DELETE FROM menu_yemekleri WHERE menu_id = %s', [menu_id])

            cursor.executemany(
                'INSERT INTO menu_yemekleri (menu_id, yemek_id) VALUES (%s, %s)',
                [(menu_id, food_id) for food_id in food_ids],
            )

    return {'menu_id': menu_id}
