"""Bir sohbet oturumunun ÇALIŞAN sağlayıcılarını takip eden ortak karışım.

NEDEN AYRI BİR MODÜL (1 Ağu 2026, iki doğrulama turu):

`OneShotSession` ve `AgySession` aynı deseni ayrı ayrı yazıyordu: tek bir
`active_provider` yuvası, ve `close_session` yalnız o yuvayı iptal ediyordu.
Aynı sohbette iki tur üst üste binerse ikincisi birincinin referansını eziyor,
Durdur yalnız sonuncuya ulaşıyor ve BİRİNCİ çocuk süreç sahipsiz kalıyordu.

Önce yalnız `OneShotSession` düzeltildi; ikinci doğrulama turu `AgySession`'ın
aynı kusuru taşıdığını ölçtü (`agy_concurrent_stop_orphan.py`). Ders: bir sınıfı
kapatmak, o sınıfın kaç kopyası olduğunu SAYMAYI gerektiriyor. Üçüncü bir kopya
doğmasın diye davranış tek yere alındı.

Eşzamanlı iki turu engelleyen bir kod YOK; "arayüz aynı anda tek tur gönderir"
bir alışkanlık, enforcement değil. Bu yüzden bulgu demote edilmedi: Durdur'un
kapsamı genişletildi.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


class SaglayiciSahipligi:
    """`active_provider` atamalarını bir KÜMEDE biriktirir; Durdur hepsini kapsar.

    Çağrı yerleri değişmiyor: `session.active_provider = p` yazan kod aynı
    kalıyor, setter kümeyi kendisi besliyor.
    """

    def _sahiplik_kur(self) -> None:
        self._active_provider = None
        self._tum_saglayicilar = set()
        self._kapandi = False

    @property
    def active_provider(self):
        return self._active_provider

    @active_provider.setter
    def active_provider(self, saglayici):
        self._active_provider = saglayici
        if saglayici is None:
            return

        # ⚠️ ÖLÇÜT "returncode DOLU MU", "süreç var mı" DEĞİL. İlk yazımda süreci
        # henüz doğmamış (`_active_process is None`) bir sağlayıcı da "bitmiş"
        # sayılıp atılıyordu — guard tam da korumak istediği pencerede onu
        # düşürüyordu. Kendi testi yakaladı.
        def _bitti(s) -> bool:
            surec = getattr(s, "_active_process", None)
            return surec is not None and surec.returncode is not None

        self._tum_saglayicilar = {s for s in self._tum_saglayicilar if not _bitti(s)}
        self._tum_saglayicilar.add(saglayici)

        # KAPANMIŞ OTURUMA GEÇ GELEN SAĞLAYICI (doğrulama turu 2,
        # `oneshot_provider_after_close.py`): `close_session` oturumu kayıttan
        # düşürüyor ama nesne yaşamaya devam ediyor. Kapanıştan SONRA atanan bir
        # sağlayıcı hiçbir Durdur yoluna görünmüyordu — çünkü artık kayıtta
        # olmayan bir nesneye asılıydı. Burada iptali kendimiz üstleniyoruz.
        if self._kapandi:
            # Warned here only: `oturumu_kapat` stamps every provider of a
            # normally closing session too, and warned on each side-chat close.
            logger.warning("[oturum] kapanmış oturuma sağlayıcı atandı — spawn engellendi")
            self._kapaninca_isaretle(saglayici)

    @staticmethod
    def _kapaninca_isaretle(saglayici) -> None:
        """Sağlayıcıyı 'oturumu kapandı' diye damgalar — SPAWN ÖNCESİ kapı.

        ⛔ ÖNCEKİ ÇÖZÜM BİR ZAMANLAMA HİLESİYDİ ve üçüncü doğrulama turu tam
        oradan girdi (`late_arrival_after_ceiling`): süreç doğana kadar 5 sn
        bekleyip BİR kez iptal çağırıyorduk; süreç o tavandan sonra doğarsa
        kapanmış oturumun artık hiçbir gözlemcisi kalmıyordu. Yarışı sonradan
        iptal ederek kazanmaya çalışmak, tavanı büyütmekle de çözülmez —
        büyütülen her tavanın ötesi vardır.

        Ölçüt değişti: yarışı kazanmaya çalışmıyoruz, YARIŞI KALDIRIYORUZ.
        Damga kalıcı; `analyze_code` spawn'dan hemen önce ona bakıyor ve
        kapanmış bir oturum için çocuk süreci HİÇ başlatmıyor. Zaten başlamış
        bir süreç varsa `oturumu_kapat` onu ayrıca iptal ediyor.
        """
        try:
            saglayici._oturum_kapandi = True
        except AttributeError:
            pass

    def iptal_edilecekler(self):
        """Durdur'un ulaşması gereken TÜM sağlayıcılar (sonuncu değil)."""
        hepsi = {s for s in self._tum_saglayicilar if s is not None}
        if self._active_provider is not None:
            hepsi.add(self._active_provider)
        return hepsi

    def kapandi_isaretle(self) -> None:
        self._kapandi = True


async def oturumu_kapat(session) -> None:
    """Bir oturumun bütün sağlayıcılarını iptal eder ve oturumu kapalı işaretler.

    Bitmiş bir sağlayıcıda `cancel_active_process` zaten `False` dönüp hiçbir şey
    yapmıyor, yani fazladan çağrının bedeli yok.
    """
    if session is None:
        return
    session.kapandi_isaretle()
    for saglayici in session.iptal_edilecekler():
        # ÖNCE damga, SONRA iptal. Sırası önemli: damga henüz spawn etmemiş bir
        # sağlayıcıyı da kapsıyor, iptal ise yalnız çalışanı durduruyor.
        # Üçüncü doğrulama turu (`owned_provider_process_after_close`) bu ikisinin
        # ayrı şeyler olduğunu gösterdi: sahibi bilinen bir sağlayıcı, tek iptal
        # çağrısından SONRA süreç doğurabiliyordu.
        SaglayiciSahipligi._kapaninca_isaretle(saglayici)
        try:
            await saglayici.cancel_active_process()
        except Exception:
            logger.warning("[oturum] bir sağlayıcı iptal edilemedi", exc_info=True)
