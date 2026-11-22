from django.urls import path
from . import views

urlpatterns = [
    path('', views.health_check, name='health_check'),
    path('giris/', views.giris, name='giris'),
    path('cikis/', views.cikis, name='cikis'),
    path('oturum/', views.oturum, name='oturum'),
    path('rezervasyonlar/', views.rezervasyon_yap, name='rezervasyon_yap'),
    path('rezervasyonlarim/', views.rezervasyonlarim, name='rezervasyonlarim'),
    path('rezervasyonlar/<int:reservation_id>/iptal/', views.rezervasyon_iptal, name='rezervasyon_iptal'),
    path('bakiye/', views.bakiye, name='bakiye'),
    path('bakiye/yukle/', views.bakiye_yukle, name='bakiye_yukle'),
    path('degerlendirmelerim/', views.degerlendirmelerim, name='degerlendirmelerim'),
    path('degerlendirmeler/', views.degerlendirme_yap, name='degerlendirme_yap'),
    path('yonetici/', views.yonetici_paneli, name='yonetici_paneli'),
    path('yonetici/menuler/kaydet/', views.yonetici_menu_kaydet, name='yonetici_menu_kaydet'),
    path('menuler/', views.menu_listesi, name='menu_listesi'),
]
