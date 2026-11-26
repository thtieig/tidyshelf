"""Regenerate the synthetic subtitle fixtures in this folder: python tests/fixtures/subs/generate.py

All dialogue is invented for tidyshelf's tests. Each file repeats its dialogue twice so it has a
plausible number of cues (the planner warns below 20).
"""

from pathlib import Path

OUT = Path(__file__).resolve().parent


def srt(cues):
    blocks = []
    for i, text in enumerate(cues, 1):
        s = i * 3
        blocks.append(f"{i}\n00:{s // 60:02}:{s % 60:02},000 --> 00:{(s + 2) // 60:02}:{(s + 2) % 60:02},500\n{text}\n")
    return "\n".join(blocks)


EN = [
    "Where were you last night? I was waiting for you.",
    "I know, I'm sorry. The train was late and my phone was dead.",
    "You could have called from the station.",
    "There was nobody there, and it was snowing so hard I could not see the road.",
    "Well, you are here now. Come in and sit by the fire.",
    "Thank you. Is your mother still awake?",
    "She went to bed an hour ago. She said to tell you that she is happy you came.",
    "I have something for her. It is in my bag, but it can wait until the morning.",
    "Do you want some tea? There is soup as well, if you are hungry.",
    "Just tea, please. I ate on the way, but it was not very good.",
    "We should talk about what happened at the house.",
    "I know we should. But not tonight. Tonight I just want to be here with you.",
    "All right. Tomorrow, then. And this time you will tell me everything.",
    "I will. I promise. Now, where did you put the good cups?",
]
SDH_EXTRA = ["[WIND HOWLING]", "(DOOR CLOSES)", "♪ SOFT MUSIC PLAYING ♪", "[FIRE CRACKLING]", "(SIGHS)"]
RO = [  # cp1250 has the cedilla forms, not the comma-below ones
    "Unde ai fost aseară? Te-am aşteptat şi nu ai venit.",
    "Ştiu, îmi pare rău. Trenul a întârziat şi telefonul meu nu mai avea baterie.",
    "Puteai să suni de la gară, dacă voiai.",
    "Nu era nimeni acolo şi ningea atât de tare că nu se vedea drumul.",
    "Bine, acum eşti aici. Intră şi stai lângă foc.",
    "Mulţumesc. Mama ta mai este trează?",
    "S-a culcat acum o oră. A spus să îţi zic că este fericită că ai venit.",
    "Am ceva pentru ea. Este în geanta mea, dar poate să aştepte până dimineaţă.",
    "Vrei un ceai? Avem şi ciorbă, dacă ţi-e foame.",
    "Doar un ceai, te rog. Am mâncat pe drum, dar nu a fost foarte bun.",
    "Trebuie să vorbim despre ce s-a întâmplat la casă.",
    "Ştiu că trebuie. Dar nu în seara asta. Acum vreau doar să fiu aici cu tine.",
    "Bine. Mâine, atunci. Şi de data asta îmi spui tot.",
    "Îţi spun tot, promit. Acum, unde ai pus ceştile cele bune?",
]
ES = [
    "¿Dónde estabas anoche? Te estuve esperando.",
    "Lo sé, lo siento. El tren llegó tarde y mi teléfono no tenía batería.",
    "Podías haber llamado desde la estación.",
    "No había nadie allí, y nevaba tanto que no se veía el camino.",
    "Bueno, ahora estás aquí. Pasa y siéntate junto al fuego.",
    "Gracias. ¿Tu madre todavía está despierta?",
    "Se fue a la cama hace una hora. Me dijo que te dijera que está contenta de que vinieras.",
    "Tengo algo para ella. Está en mi bolsa, pero puede esperar hasta mañana.",
    "¿Quieres un té? También hay sopa, si tienes hambre.",
    "Solo té, por favor. Comí por el camino, pero no estaba muy bueno.",
    "Tenemos que hablar de lo que pasó en la casa.",
    "Lo sé. Pero no esta noche. Esta noche solo quiero estar aquí contigo, señor mío.",
    "Muy bien. Mañana, entonces. Y esta vez me lo vas a contar todo.",
    "Te lo contaré todo, te lo prometo. ¿Dónde pusiste las tazas buenas?",
]
IT = [
    "Dove eri ieri sera? Ti ho aspettato.",
    "Lo so, mi dispiace. Il treno era in ritardo e il mio telefono era scarico.",
    "Potevi chiamare dalla stazione.",
    "Non c'era nessuno, e nevicava così tanto che non si vedeva la strada.",
    "Va bene, adesso sei qui. Entra e siediti vicino al fuoco.",
    "Grazie. Tua madre è ancora sveglia?",
    "È andata a letto un'ora fa. Ha detto di dirti che è felice che sei venuto.",
    "Ho una cosa per lei. È nella mia borsa, ma può aspettare fino a domani.",
    "Vuoi un tè? C'è anche la zuppa, se hai fame.",
    "Solo un tè, per favore. Ho mangiato per strada, ma non era molto buono.",
    "Dobbiamo parlare di quello che è successo nella casa.",
    "Lo so che dobbiamo. Ma non stasera. Stasera voglio solo stare qui con te.",
    "Va bene. Domani, allora. E questa volta mi dici tutto, perché ne ho bisogno.",
    "Ti dirò tutto, te lo prometto. Dove hai messo le tazze più belle?",
]


def main():
    en_sdh = []
    for i, cue in enumerate(EN * 2):
        en_sdh.append(cue)
        if i % 3 == 0:
            en_sdh.append(SDH_EXTRA[(i // 3) % len(SDH_EXTRA)])
    en_sdh.append("Subtitles by www.example.com")
    (OUT / "en_plain.srt").write_text(srt(EN * 2), encoding="utf-8")
    (OUT / "en_sdh.srt").write_text(srt(en_sdh), encoding="utf-8")
    (OUT / "ro.srt").write_bytes(srt(RO * 2).encode("cp1250"))
    (OUT / "es.srt").write_bytes(srt(ES * 2).encode("cp1252"))
    (OUT / "it.srt").write_bytes(b"\xef\xbb\xbf" + srt(IT * 2).encode("utf-8"))
    ass = ["[Script Info]", "Title: test", "ScriptType: v4.00+", "", "[Events]",
           "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for i, cue in enumerate(EN * 2):
        ass.append(f"Dialogue: 0,0:00:{i * 2:02}.00,0:00:{i * 2 + 1:02}.00,Default,,0,0,0,,"
                   f"{{\\i1}}{cue.replace(', ', ',' + chr(92) + 'N', 1)}{{\\i0}}")
    (OUT / "en.ass").write_text("\n".join(ass) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
