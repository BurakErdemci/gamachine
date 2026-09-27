"""Turn text of a read-only side question (the side panel over a main chat).

Every provider path builds a side turn from here, never from the generic
handoff text. Live test 27 Sep 2026: with the question FIRST and the main
chat's history after it, under headers saying "continue where you left off"
and "this history is enough context", OpenCode answered the main chat's
in-flight request (the last line of that history) instead of the side
question. So the order is fixed: instruction, reference-only history, the
main chat's running request labelled as not ours, its live answer, earlier
side Q/A, and the side question LAST.
"""
from dataclasses import dataclass

SIDE_INSTRUCTION = (
    "[YAN SORU — SALT OKUNUR] Kullanıcı bunu ana sohbet sürerken yan panelden "
    "soruyor. Bu ayrı bir sohbet: ana sohbetin işini sen yürütmüyorsun. Yalnız en "
    "sondaki yan soruyu, aşağıdaki konuşmada görünenlere dayanarak tek bir cevapta "
    "doğrudan cevapla. Dosya okuma gibi salt okunur araçları yalnız soru konuşmada "
    "olmayan bir bilgiyi gerçekten gerektiriyorsa kullan. \"Bakayım\", \"şunlara "
    "bakayım\" gibi girişler yapma; bilmiyorsan bilmediğini söyle. Dosya "
    "yazma/düzenleme/silme, komut çalıştırma, Unity'de değişiklik ve hafızaya "
    "kaydetme YASAK ve reddedilir; deneme.\n"
    "[SIDE QUESTION — READ-ONLY] Answer only the final side question, directly and "
    "in one response, from what the conversation shows. Use read-only tools only "
    "if the question truly needs information the conversation lacks. No \"let me "
    "look\" preambles; if you do not know, say so. Never write or edit files, run "
    "commands, change Unity or save memory."
)

MAIN_HISTORY_HEADER = (
    "[ANA SOHBETİN GEÇMİŞİ — YALNIZ BAŞVURU İÇİN] Bu, başka bir sohbetin (ana "
    "sohbetin) geçmişi. Onu SÜRDÜRME ve içindeki istekleri YERİNE GETİRME; yalnız "
    "yan soruyu cevaplamak için bilgi kaynağı olarak kullan.\n"
    "[MAIN CHAT HISTORY — REFERENCE ONLY] It belongs to another chat: do not "
    "continue it and do not carry out requests found in it."
)

# Sub-header of the transcript lines inside the history block; replaces the
# generic "continue where you left off" header of the handoff builder.
MAIN_TRANSCRIPT_LABEL = "[ANA SOHBETİN MESAJLARI]"

IN_FLIGHT_HEADER = (
    "[ANA SOHBETTE ŞU AN İŞLENEN İSTEK — bu istek şu anda ana sohbette ele "
    "alınıyor; onu CEVAPLAMA, yalnız bağlam olarak bil]\n"
    "[BEING HANDLED RIGHT NOW IN THE MAIN CHAT — do not answer it]"
)
IN_FLIGHT_CAP = 4000

QUESTION_HEADER = "[YAN SORU — CEVAPLANACAK TEK SORU]"
FINAL_REMINDER = "(Yalnız bu yan soruyu cevapla. / Answer only this question.)"

# API loops put the context in their system prompt, whose working rules start
# with "explore the project first"; for a side turn that slot says the user
# message decides.
API_SYSTEM_CONTEXT = (
    "[YAN SORU] Bu tur, ana sohbetin yanında sorulmuş salt okunur bir yan soru. "
    "Kullanıcı mesajındaki yan soru talimatı yukarıdaki çalışma prensiplerinden "
    "önce gelir: projeyi keşfetme, kod yazma; yalnız en sondaki yan soruyu cevapla."
)


@dataclass(frozen=True)
class SideTurn:
    """The parts of one side turn; blocks other than `question` are pre-labelled
    except `in_flight_request`, which is the main chat's raw running request."""
    question: str
    main_history: str = ""
    in_flight_request: str = ""
    live_answer: str = ""
    side_history: str = ""

    def _question_block(self) -> str:
        return f"{QUESTION_HEADER}\n{self.question}\n\n{FINAL_REMINDER}"

    def fits(self, context_cap: int) -> bool:
        """Whether the parts that are never trimmed fit `context_cap`."""
        return len(SIDE_INSTRUCTION) + len(_SEP) + len(self._question_block()) <= context_cap

    def text(self, full: bool, context_cap: "int | None" = None) -> str:
        """`full` for a turn whose provider holds none of this side chat yet;
        otherwise the provider's own session already has the history and the
        earlier side Q/A, and only what changes per turn is sent again.

        `context_cap` bounds the WHOLE message (the one-shot CLIs pass it on
        the command line; Codex mentionaudit, 27 Sep 2026 measured 37,593
        characters when only the history was budgeted). The instruction and
        the question are never trimmed; the rest is fitted in the order the
        question needs it: running request, live answer, earlier side Q/A,
        main history. Raises SideTurnTooLong when the fixed parts alone do
        not fit.
        """
        request = (self.in_flight_request or "").strip()
        if len(request) > IN_FLIGHT_CAP:
            request = request[:IN_FLIGHT_CAP] + _TRIM_TAIL
        history = self.main_history if full else ""
        side = self.side_history if full else ""
        question = self._question_block()
        blocks = {
            "history": f"{MAIN_HISTORY_HEADER}\n{history}" if history else "",
            "request": f"{IN_FLIGHT_HEADER}\n{request}" if request else "",
            "live": self.live_answer or "",
            "side": side or "",
        }
        if context_cap is not None:
            if not self.fits(context_cap):
                raise SideTurnTooLong(side_too_long_message(context_cap))
            room = context_cap - len(SIDE_INSTRUCTION) - len(_SEP) - len(question)
            blocks["request"], room = _fit(IN_FLIGHT_HEADER, request, room, False, _TRIM_TAIL)
            blocks["live"], room = _fit(*_split_label(self.live_answer or ""), room, True, _TRIM_LIVE)
            blocks["side"], room = _fit(*_split_label(side or ""), room, True, _TRIM_SIDE)
            blocks["history"], room = _fit(MAIN_HISTORY_HEADER, history, room, True, _TRIM_HISTORY)
        parts = [SIDE_INSTRUCTION]
        parts += [blocks[k] for k in ("history", "request", "live", "side") if blocks[k]]
        parts.append(question)
        return _SEP.join(parts)


_SEP = "\n\n"
_TRIM_TAIL = " …[kısaltıldı]"
_TRIM_LIVE = "…[başı kısaltıldı]\n"
_TRIM_SIDE = "…[daha eski yan sorular kısaltıldı]\n"
_TRIM_HISTORY = "…[ana sohbetin eski kısmı kırpıldı]\n"
# A block cut below this many characters no longer tells the model anything;
# it is dropped instead of sent as a fragment.
_MIN_FRAGMENT = 200


class SideTurnTooLong(ValueError):
    """The instruction and the question alone exceed the cap: nothing that may
    be trimmed is left, so the turn is refused instead of spawned."""


def side_too_long_message(context_cap: int) -> str:
    return (f"Yan soru gönderilemedi: soru, yan soru talimatıyla birlikte bu "
            f"sağlayıcının komut satırı sınırına ({context_cap} karakter) sığmıyor. "
            f"Soruyu kısaltıp tekrar sor.")


def _split_label(block: str) -> "tuple[str, str]":
    """A pre-labelled block is `label\\nbody`; one without a newline is all body."""
    if "\n" not in block:
        return "", block
    label, _, body = block.partition("\n")
    return label, body


def _fit(label: str, body: str, room: int, keep_tail: bool, mark: str) -> "tuple[str, int]":
    """(`label\\nbody` cut to `room` incl. its separator, room left). The cut
    keeps the body's tail or head; a block too small to be useful is dropped."""
    if not body:
        return "", room
    prefix = f"{label}\n" if label else ""
    whole = prefix + body
    if len(_SEP) + len(whole) <= room:
        return whole, room - len(_SEP) - len(whole)
    avail = room - len(_SEP) - len(prefix) - len(mark)
    if avail < _MIN_FRAGMENT:
        return "", room
    block = prefix + (mark + body[-avail:] if keep_tail else body[:avail] + mark)
    return block, room - len(_SEP) - len(block)
