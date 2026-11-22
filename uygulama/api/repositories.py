import json
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.serializers.json import DjangoJSONEncoder
from django.db import connections, transaction

from .redis_client import get_redis_client


cache_logger = logging.getLogger('api.cache')
MENU_INDEX_KEY = 'menu:index'


def _menu_cache_key(menu):
    menu_date = menu['tarih']
    if hasattr(menu_date, 'isoformat'):
        menu_date = menu_date.isoformat()[:10]
    return f"menu:{menu['yemekhane_id']}:{menu_date}:{menu['ogun_id']}"


def _menu_ttl(menu_date):
    if hasattr(menu_date, 'date') and not isinstance(menu_date, type(datetime.now().date())):
        menu_date = menu_date.date()
    if isinstance(menu_date, str):
        menu_date = datetime.fromisoformat(menu_date[:10]).date()
    zone = ZoneInfo('Europe/Istanbul')
    expires_at = datetime.combine(menu_date + timedelta(days=1), time.min, tzinfo=zone)
    return max(1, int((expires_at - datetime.now(zone)).total_seconds()))


def get_menus():
    redis_client = get_redis_client()
    cached_keys = redis_client.get(MENU_INDEX_KEY)
    if cached_keys:
        cached_menus = [redis_client.get(key) for key in json.loads(cached_keys)]
        if cached_menus and all(cached_menus):
            parsed_menus = [json.loads(menu) for menu in cached_menus]
            if all(
                'alerjen_idler' in yemek
                for menu in parsed_menus
                for yemek in menu.get('yemekler', [])
            ):
                cache_logger.info('Menü kaynağı: Redis')
                return parsed_menus
            redis_client.delete(MENU_INDEX_KEY)

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
                   d.alerjenler,
                   d.alerjen_idler
            FROM v_menu_detay AS d
            JOIN v_menu_doluluk AS f ON f.menu_id = d.menu_id
            WHERE d.tarih >= CURDATE()
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
            'alerjen_idler': [
                int(item) for item in (row['alerjen_idler'] or '').split(',') if item
            ],
        })
        menus[menu_id]['toplam_kalori'] += row['kalori']

    result = list(menus.values())
    cache_keys = []
    minimum_ttl = None
    for menu in result:
        key = _menu_cache_key(menu)
        ttl = _menu_ttl(menu['tarih'])
        redis_client.setex(key, ttl, json.dumps(menu, cls=DjangoJSONEncoder))
        cache_keys.append(key)
        minimum_ttl = ttl if minimum_ttl is None else min(minimum_ttl, ttl)
    if cache_keys:
        redis_client.setex(MENU_INDEX_KEY, minimum_ttl, json.dumps(cache_keys))
    cache_logger.info('Menü kaynağı: Veritabanı')
    return result


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


def make_reservation(user_id, menu_id, portion):
    """Rezervasyonu kaynak veritabanındaki yordamla yapar."""
    with connections['default'].cursor() as cursor:
        cursor.execute('CALL sp_rezervasyon_yap(%s, %s, %s)', [user_id, menu_id, portion])
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
        'porsiyon': sonuc[3],
    }


def get_user_reservations(user_id):
    """Kullanıcının rezervasyonlarını salt okunur replikadan getirir."""
    with connections['replica'].cursor() as cursor:
        cursor.execute(
            """
            SELECT r.id AS rezervasyon_id, r.menu_id, r.durum, r.porsiyon, r.ucret,
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


def get_allergen_profile(user_id):
    """Alerjen seçeneklerini ve kullanıcının tercihlerini salt okunur replikadan alır."""
    with connections['replica'].cursor() as cursor:
        cursor.execute('SELECT id, ad FROM alerjenler ORDER BY ad')
        allergens = [{'id': row[0], 'ad': row[1]} for row in cursor.fetchall()]
        cursor.execute(
            'SELECT alerjen_id FROM kullanici_alerjenleri WHERE kullanici_id = %s ORDER BY alerjen_id',
            [user_id],
        )
        selected = [row[0] for row in cursor.fetchall()]
    return {'alerjenler': allergens, 'secili_alerjen_idleri': selected}


def save_allergen_profile(user_id, allergen_ids):
    """Alerjen tercihlerini kaynakta tek transaction ile günceller."""
    with transaction.atomic(using='default'):
        with connections['default'].cursor() as cursor:
            cursor.execute('SELECT id FROM alerjenler')
            allowed = {row[0] for row in cursor.fetchall()}
            if not set(allergen_ids).issubset(allowed):
                raise ValueError('Geçersiz alerjen seçimi.')
            cursor.execute(
                'DELETE FROM kullanici_alerjenleri WHERE kullanici_id = %s',
                [user_id],
            )
            if allergen_ids:
                cursor.executemany(
                    'INSERT INTO kullanici_alerjenleri (kullanici_id, alerjen_id) VALUES (%s, %s)',
                    [(user_id, allergen_id) for allergen_id in allergen_ids],
                )
    return {'secili_alerjen_idleri': allergen_ids}


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
    old_cache_key = None
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
                    'SELECT yemekhane_id, tarih, ogun_id FROM menuler WHERE id = %s',
                    [menu_id],
                )
                old_menu = cursor.fetchone()
                if old_menu:
                    old_cache_key = f'menu:{old_menu[0]}:{old_menu[1].isoformat()}:{old_menu[2]}'
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

    # Commit tamamlandıktan sonra eski ve yeni menü anahtarlarını temizle.
    try:
        redis_client = get_redis_client()
        new_cache_key = f'menu:{restaurant_id}:{menu_date}:{meal_id}'
        keys_to_delete = [MENU_INDEX_KEY, new_cache_key]
        if old_cache_key:
            keys_to_delete.append(old_cache_key)
        redis_client.delete(*keys_to_delete)
        cache_logger.info('Yönetici menü kaydetti; ilgili menü önbelleği silindi.')
    except Exception:
        cache_logger.exception('Menü kaydedildi ancak Redis önbelleği temizlenemedi.')

    return {'menu_id': menu_id}
