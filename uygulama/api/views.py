import json
from datetime import date
from decimal import Decimal, InvalidOperation

import bcrypt
from django.db import DatabaseError, IntegrityError
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .repositories import (
    find_active_user_by_email,
    find_active_user_by_id,
    get_reviewable_menus,
    get_user_reservations,
    get_menus,
    cancel_reservation,
    create_review,
    get_balance_and_movements,
    load_balance,
    make_reservation,
    get_admin_panel_data,
    save_admin_menu,
)
from .sessions import start_redis_session, end_redis_session


@require_GET
@ensure_csrf_cookie
def health_check(request):
    return JsonResponse({
        'durum': 'aktif',
        'mesaj': 'Yemekhane Web API servisi calisiyor.'
    })

@require_GET
def menu_listesi(request):
    try:
        return JsonResponse({'menuler': get_menus()}, safe=False)
    except Exception as hata:
        return JsonResponse({'hata': str(hata)}, status=500)


@require_GET
def oturum(request):
    kullanici_id = request.user_id
    if kullanici_id is None:
        return JsonResponse({'hata': 'Önce giriş yapmalısınız.'}, status=401)

    kullanici = find_active_user_by_id(kullanici_id)
    if kullanici is None:
        end_redis_session(request)
        return JsonResponse({'hata': 'Kullanıcı hesabı bulunamadı.'}, status=401)

    return JsonResponse({'kullanici': kullanici})


@require_POST
def rezervasyon_yap(request):
    kullanici_id = request.user_id
    if kullanici_id is None:
        return JsonResponse({'hata': 'Önce giriş yapmalısınız.'}, status=401)

    try:
        veri = json.loads(request.body)
        menu_id = veri.get('menu_id') if isinstance(veri, dict) else None
        if isinstance(menu_id, bool):
            raise ValueError
        menu_id = int(menu_id)
        if menu_id < 1:
            raise ValueError
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        return JsonResponse({'hata': 'Geçerli bir menü seçilmelidir.'}, status=400)

    try:
        sonuc = make_reservation(kullanici_id, menu_id)
        return JsonResponse(sonuc)
    except DatabaseError as hata:
        mesaj = hata.args[1] if len(hata.args) > 1 else str(hata)
        return JsonResponse({'hata': str(mesaj)}, status=400)


@require_GET
def rezervasyonlarim(request):
    kullanici_id = request.user_id
    if kullanici_id is None:
        return JsonResponse({'hata': 'Önce giriş yapmalısınız.'}, status=401)

    try:
        return JsonResponse({'rezervasyonlar': get_user_reservations(kullanici_id)})
    except Exception as hata:
        return JsonResponse({'hata': str(hata)}, status=500)


@require_POST
def rezervasyon_iptal(request, reservation_id):
    kullanici_id = request.user_id
    if kullanici_id is None:
        return JsonResponse({'hata': 'Önce giriş yapmalısınız.'}, status=401)

    try:
        sonuc = cancel_reservation(kullanici_id, reservation_id)
        return JsonResponse(sonuc)
    except DatabaseError as hata:
        mesaj = hata.args[1] if len(hata.args) > 1 else str(hata)
        return JsonResponse({'hata': str(mesaj)}, status=400)


@require_GET
def bakiye(request):
    kullanici_id = request.user_id
    if kullanici_id is None:
        return JsonResponse({'hata': 'Önce giriş yapmalısınız.'}, status=401)

    try:
        sonuc = get_balance_and_movements(kullanici_id)
        if sonuc is None:
            return JsonResponse({'hata': 'Kullanıcı hesabı bulunamadı.'}, status=404)
        return JsonResponse(sonuc)
    except Exception as hata:
        return JsonResponse({'hata': str(hata)}, status=500)


@require_POST
def bakiye_yukle(request):
    kullanici_id = request.user_id
    if kullanici_id is None:
        return JsonResponse({'hata': 'Önce giriş yapmalısınız.'}, status=401)

    try:
        veri = json.loads(request.body)
        tutar_degeri = veri.get('tutar') if isinstance(veri, dict) else None
        tutar = Decimal(str(tutar_degeri))
        if not tutar.is_finite() or tutar <= 0:
            raise InvalidOperation
        if tutar.quantize(Decimal('0.01')) != tutar:
            return JsonResponse({'hata': 'Tutar en fazla iki ondalık basamak içerebilir.'}, status=400)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError, InvalidOperation):
        return JsonResponse({'hata': 'Geçerli bir yükleme tutarı girilmelidir.'}, status=400)

    try:
        sonuc = load_balance(kullanici_id, tutar)
        sonuc['tutar'] = str(tutar)
        return JsonResponse(sonuc)
    except DatabaseError as hata:
        mesaj = hata.args[1] if len(hata.args) > 1 else str(hata)
        return JsonResponse({'hata': str(mesaj)}, status=400)


@require_GET
def degerlendirmelerim(request):
    kullanici_id = request.user_id
    if kullanici_id is None:
        return JsonResponse({'hata': 'Önce giriş yapmalısınız.'}, status=401)

    try:
        return JsonResponse({'menuler': get_reviewable_menus(kullanici_id)})
    except Exception as hata:
        return JsonResponse({'hata': str(hata)}, status=500)


@require_POST
def degerlendirme_yap(request):
    kullanici_id = request.user_id
    if kullanici_id is None:
        return JsonResponse({'hata': 'Önce giriş yapmalısınız.'}, status=401)

    try:
        veri = json.loads(request.body)
        if not isinstance(veri, dict):
            raise ValueError
        menu_id = veri.get('menu_id')
        puan = veri.get('puan')
        yorum = veri.get('yorum', '')
        if isinstance(menu_id, bool) or isinstance(puan, bool):
            raise ValueError
        menu_id = int(menu_id)
        puan = int(puan)
        if menu_id < 1 or puan < 1 or puan > 5:
            raise ValueError
        if not isinstance(yorum, str) or len(yorum) > 500:
            return JsonResponse({'hata': 'Yorum en fazla 500 karakter olabilir.'}, status=400)
        yorum = yorum.strip()
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        return JsonResponse({'hata': 'Menü ve 1 ile 5 arasında bir puan seçmelisiniz.'}, status=400)

    try:
        return JsonResponse(create_review(kullanici_id, menu_id, puan, yorum))
    except ValueError as hata:
        return JsonResponse({'hata': str(hata)}, status=400)
    except DatabaseError as hata:
        mesaj = hata.args[1] if len(hata.args) > 1 else str(hata)
        return JsonResponse({'hata': str(mesaj)}, status=400)


def yonetici_id(request):
    kullanici_id = request.user_id
    if kullanici_id is None:
        return None, JsonResponse({'hata': 'Önce giriş yapmalısınız.'}, status=401)
    kullanici = find_active_user_by_id(kullanici_id)
    if kullanici is None:
        return None, JsonResponse({'hata': 'Kullanıcı hesabı bulunamadı.'}, status=401)
    if kullanici['rol'] != 'yonetici':
        return None, JsonResponse({'hata': 'Bu ekran yalnızca yöneticiler içindir.'}, status=403)
    return kullanici_id, None


@require_GET
def yonetici_paneli(request):
    kullanici_id, hata_yaniti = yonetici_id(request)
    if hata_yaniti:
        return hata_yaniti
    try:
        return JsonResponse(get_admin_panel_data())
    except Exception as hata:
        return JsonResponse({'hata': str(hata)}, status=500)


@require_POST
def yonetici_menu_kaydet(request):
    kullanici_id, hata_yaniti = yonetici_id(request)
    if hata_yaniti:
        return hata_yaniti

    try:
        veri = json.loads(request.body)
        if not isinstance(veri, dict):
            raise ValueError

        menu_id = veri.get('menu_id')
        if menu_id in ('', None):
            menu_id = None
        else:
            if isinstance(menu_id, bool):
                raise ValueError
            menu_id = int(menu_id)
            if menu_id < 1:
                raise ValueError

        def pozitif_tamsayi(alan):
            deger = veri.get(alan)
            if isinstance(deger, bool):
                raise ValueError
            sayi = int(deger)
            if sayi < 1:
                raise ValueError
            return sayi

        restaurant_id = pozitif_tamsayi('yemekhane_id')
        meal_id = pozitif_tamsayi('ogun_id')
        menu_date = date.fromisoformat(veri.get('tarih', ''))

        def para_tutari(alan):
            tutar = Decimal(str(veri.get(alan)))
            if not tutar.is_finite() or tutar < 0:
                raise ValueError
            if tutar.quantize(Decimal('0.01')) != tutar:
                raise ValueError
            return tutar

        student_price = para_tutari('ogrenci_fiyati')
        staff_price = para_tutari('personel_fiyati')
        food_ids = veri.get('yemek_idleri')
        if not isinstance(food_ids, list) or not food_ids:
            raise ValueError
        if any(isinstance(food_id, bool) for food_id in food_ids):
            raise ValueError
        food_ids = list(dict.fromkeys(int(food_id) for food_id in food_ids))
        if any(food_id < 1 for food_id in food_ids):
            raise ValueError
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError, InvalidOperation):
        return JsonResponse({'hata': 'Tarih, yemekhane, öğün, fiyatlar ve en az bir yemek seçilmelidir.'}, status=400)

    try:
        sonuc = save_admin_menu(
            kullanici_id, menu_id, restaurant_id, menu_date, meal_id,
            student_price, staff_price, food_ids,
        )
        return JsonResponse(sonuc)
    except PermissionError as hata:
        return JsonResponse({'hata': str(hata)}, status=403)
    except ValueError as hata:
        return JsonResponse({'hata': str(hata)}, status=400)
    except IntegrityError as hata:
        if hata.args and hata.args[0] == 1062:
            return JsonResponse({'hata': 'Bu yemekhane, tarih ve öğün için menü zaten var.'}, status=400)
        return JsonResponse({'hata': 'Menü kaydedilemedi. Seçimleri kontrol edin.'}, status=400)
    except DatabaseError as hata:
        mesaj = hata.args[1] if len(hata.args) > 1 else str(hata)
        return JsonResponse({'hata': str(mesaj)}, status=400)


@require_POST
def giris(request):
    try:
        veri = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'hata': 'İstek JSON biçiminde olmalıdır.'}, status=400)

    if not isinstance(veri, dict):
        return JsonResponse({'hata': 'İstek içinde e-posta ve şifre bulunmalıdır.'}, status=400)

    eposta = veri.get('eposta', '')
    sifre = veri.get('sifre', '')
    if not isinstance(eposta, str) or not isinstance(sifre, str):
        return JsonResponse({'hata': 'E-posta ve şifre metin olmalıdır.'}, status=400)
    eposta = eposta.strip().lower()
    if not eposta or not sifre:
        return JsonResponse({'hata': 'E-posta ve şifre zorunludur.'}, status=400)

    kullanici = find_active_user_by_email(eposta)
    if kullanici is None:
        return JsonResponse({'hata': 'E-posta veya şifre hatalı.'}, status=401)

    try:
        gecerli = bcrypt.checkpw(
            sifre.encode('utf-8'),
            kullanici['sifre_hash'].encode('utf-8'),
        )
    except (ValueError, TypeError):
        gecerli = False

    if not gecerli:
        return JsonResponse({'hata': 'E-posta veya şifre hatalı.'}, status=401)

    kullanici.pop('sifre_hash')
    start_redis_session(request, kullanici['id'])
    return JsonResponse({'kullanici': kullanici})


@require_POST
def cikis(request):
    end_redis_session(request)
    return JsonResponse({'mesaj': 'Çıkış yapıldı.'})
