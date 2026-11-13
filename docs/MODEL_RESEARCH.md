# מודלים להורדה ולהרצה מקומית — 13 בנובמבר 2026

החיפוש כולל מודלים פתוחים עם רישיון MIT/Apache, וגם משקולות זמינות עם רישיון מוגבל, המסומנות בנפרד. זו רשימת מועמדים רחבה לצורכי המערכת; היא אינה טענה שכל מודל שקיים ברשת נסקר. תמיכה רב־לשונית כללית אינה הוכחה ל־OCR תקין בעברית. גם מודל שאינו קורא עברית יכול לשמש **רק לחיתוך ספרות**, בלי לעדכן מלל עברי.

## מה נבדק על מעבד בפועל

סביבת Linux x64, מעבד AMD EPYC, עד שני תהליכוני חישוב למנוע, מגבלת זיכרון 8GiB וללא GPU. המספרים הם מדידות כאן, לא הבטחה למחשב Windows אחר. RSS כולל את תהליך המנוע אחרי ההרצה; אינו שיא זיכרון המערכת. זמני חיתוך אינם זמני עמוד שלם. הבדיקות קטנות, זמני הריצה כוללים חימום שונה, ואין להשתמש בהם לדירוג איכות כללי.

| מודל | משקולות שהורדו בקירוב | זיכרון תהליך שנמדד | זמן לחיתוך ספרות | תוצאה ומגבלה |
|---|---:|---:|---:|---|
| Tesseract Fast / Best, עברית ואנגלית | MB בודדים עד עשרות MB | לא נמדד | משתנה | בסיס לעברית מודפסת; אינו מנוע ייעודי לכתב יד |
| Tesseract heb_rivok / lite | 5.7MB / 3.4MB | לא נמדד | עמודים: 0.62–1.16 / 0.35–0.66 שניות | שתי הגרסאות נטענו. המלאה קראה נכון עברית ומספר עם אפסים מובילים בבדיקה סינתטית; lite הוסיפה ספרה באותה בדיקה. בעמודי אמת עדיין יש שגיאות |
| MNIST ONNX | 26KB | לא נמדד | מהיר | ספרה בודדת; הפרדת ספרות נוגעות וקישוטים נכשלת לעיתים |
| PP-OCRv5 English mobile ONNX | 7.8MB | לא נמדד | כ־0.04–0.11 שניות | רצפי ספרות בלבד; אין עברית במילון המודל הזה |
| PP-OCRv6 tiny / small ONNX | 4.3MB / 21MB | לא נמדד | small: כ־0.02 שניות | רצפי ספרות בלבד; גרסת tiny שגתה ביותר חיתוכים בניסוי |
| Indra RTL | כ־76MB לחבילה | לא נמדד | לא הושווה בתנאים זהים | עברית נוסתה; רישיון משקולות מוגבל |
| Qwen3-VL-2B Q4 + projector | כ־1.5GB | כ־3GiB | כ־1.4–2.5 שניות | קרא ספרות אך הפך עברית בניסוי; מוגבל במערכת לספרות |
| Qwen3-VL-4B Q4 + projector | כ־3GB | כ־4.5GiB | כ־3–4 שניות | סדר עברית תקין בניסוי קצר, אך טעויות מילים; לא פתר את הצ׳ק הקשה |
| GLM-OCR Q8 + projector | כ־1.43GB | כ־1.48GiB | 0.77–1.65 שניות | הורד ונבדק על 11 חיתוכים; עברית לא אומתה |
| PaddleOCR-VL-1.6 GGUF + projector | כ־1.82GB | כ־1.92GiB | 5.44–6.70 שניות | הורד ונבדק על 11 חיתוכים; עברית לא אומתה כאן |
| Qwen3.5-0.8B Q8 + projector | כ־1.02GB | כ־1.54GiB | 0.86–1.69 שניות | הורד ונבדק על 11 חיתוכים; השמיט ספרות בחלקם; עברית לא אומתה |
| LightOnOCR-2-1B Q8 + projector | כ־1.09GB | כ־1.62GiB | 1.21–11.93 שניות | הורד ונבדק על 11 חיתוכים; הוסיף לעיתים סימני עיצוב; עברית לא אומתה |
| Rivok Hebrew SVTRv2 cascade FP32 | כ־182MB לחבילה זו | כ־1.00GiB, שיא לתהליך הבדיקה | עמודים: 12.7–29.8 שניות | סדר עברית לוגי, שגיאות אותיות וספרות; מיועד בעיקר לדפוס |
| Rivok Hebrew SVTRv2 CTC INT8 | כ־20MB לרכיב CTC | לא נטען | — | ONNX Runtime 1.23.2 ב־Linux נכשל: ConvInteger לא נתמך; אין להכריז על תאימות Windows לפני בדיקה |
| TrOCR small handwritten ONNX | כ־63MB בכימות שנבדק | לא נמדד | — | ניסיון מפענח ONNX ישיר החזיר מלל שגוי בחיתוכי ספרות; גם ניסיון FP32 לא סיפק שיפור. לא שולב |
| SimpleHTR ONNX | כ־13MB | לא נמדד | כ־0.003–0.005 שניות | הורד ונבדק; תוצאות הספרות לא שימושיות בדוגמה. אין עברית |

בארבעת המודלים החדשים GLM / Paddle VL / Qwen3.5 / LightOn, כל אחד הצליח באחד משני מספרי הצ׳ק שהמשתמש אישר ונכשל בשני. רק שני רצפים הם ערכי אמת מאושרים; שאר הקריאות הן הצעות. **אין כאן מדידת 99%**, ואין להסיק ממנה שיעור הצלחה כללי. קבצי הלקוח, החיתוכים והתוצאות המפורטות נשארים מחוץ ל־Git.

## מאגר המועמדים ומקורות ההורדה

״לא אומתה״ פירושו שאין אישור לאיכות עברית במערכת, גם אם היצרן מציין multilingual. גדלי פרמטרים מעוגלים אינם זיכרון ריצה. למודלים שלא הורצו כאן נדרשת בדיקת runtime, דרייברים וזיכרון; עצם הורדת המשקולות אינה הוכחת התאמה למחשב חלש.

| מודל / מקור רשמי | עברית ותפקיד אפשרי | רישיון ומשמעות מקומית |
|---|---|---|
| [Tesseract tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast), [tessdata_best](https://github.com/tesseract-ocr/tessdata_best) | `heb` + `eng`, דפוס | Apache-2.0; CPU, מותקן |
| [Tesseract heb_rivok / lite](https://huggingface.co/Rivok/tesseract-heb-rivok) | דפוס עברי; full כולל אנגלית וספרות, lite דורש `+eng` לאנגלית | Apache-2.0; שתי גרסאות הכימות הורדו ונבדקו על CPU; אינן כתב יד מאומת |
| [Rivok Hebrew](https://huggingface.co/Rivok/paddleocr-hebrew) | דפוס עברי + אנגלית + ספרות; SVTRv2, light, mobile-word, v5, v6 | Apache-2.0 למשקולות וקוד; ONNX. קוד האימון אינו מפורסם. גרסת mobile: כ־7.4MB לרכיב הזיהוי בלבד; full cascade נבדק |
| [cyttic Hebrew handwriting](https://huggingface.co/cyttic/hebrew-ocr-android) | מועמד לכתב יד עברי, ONNX; base INT8 כ־106MB, גם large | MIT לקובצי המחבר; רכיבי DictaBERT נפרדים CC BY 4.0. לא נבדק; קישור מאגר Android שבכרטיס המודל החזיר מאגר ריק בעת הבדיקה |
| [TrOCR Hebrew experimental](https://huggingface.co/sivan22/testing-trOCR-hebrew-handwritten) | ניסוי בכתב יד עברי | רישיון לא צוין בכרטיס שנבדק; לא הופעל ולא מאושר להפצה |
| [GigaPDF Hebrew ONNX](https://huggingface.co/ronylicha/gigapdf-ocr-hebrew) | מועמד עברית לדפוס | תג רישיון `other`; נדרשת בדיקת התנאים לפני שילוב |
| [Indra](https://huggingface.co/datalab-to/indra-ocr) | רכיב RTL לעברית | רישיון משקולות Modified OpenRAIL-M; תנאים מסחריים נפרדים מקוד Apache |
| [PP-OCRv5 mobile English ONNX](https://huggingface.co/PaddlePaddle/en_PP-OCRv5_mobile_rec_onnx) | ספרות בלבד במערכת | Apache-2.0; CPU, מותקן |
| [PP-OCRv6 small ONNX](https://huggingface.co/PaddlePaddle/PP-OCRv6_small_rec_onnx) | ספרות בלבד במערכת | Apache-2.0; CPU, מותקן. אין להציג את משפחת PP-OCR כולה כתומכת עברית |
| [Qwen3-VL](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF) | 2B לספרות; 4B להצעות מלל שנבדקות ידנית | Apache-2.0; GGUF/llama.cpp, CPU נבדק |
| [Qwen3.5-0.8B](https://huggingface.co/Qwen/Qwen3.5-0.8B), גם 2B/4B | מועמד ראייה קטן; 0.8B נבדק לספרות | Apache-2.0. [המרת GGUF שנבדקה](https://huggingface.co/unsloth/Qwen3.5-0.8B-GGUF); דגמים גדולים יותר טרם נבדקו |
| [GLM-OCR](https://huggingface.co/zai-org/GLM-OCR) | OCR ייעודי; שימוש בספרות עד אימות עברית | MIT; [GGUF שנבדק](https://huggingface.co/ggml-org/GLM-OCR-GGUF) |
| [PaddleOCR-VL-1.6](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6) | OCR רב־לשוני; עברית דורשת בדיקה עצמאית | Apache-2.0; [GGUF רשמי שנבדק](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF) |
| [LightOnOCR-2-1B](https://huggingface.co/lightonai/LightOnOCR-2-1B) | OCR ייעודי; אין הוכחת עברית | Apache-2.0; [GGUF שנבדק](https://huggingface.co/ggml-org/LightOnOCR-2-1B-GGUF) |
| [Microsoft TrOCR](https://huggingface.co/microsoft/trocr-small-handwritten) | כתב יד לטיני/ספרות; לא עברית בגרסה זו | MIT; small/base/large. ניסיון small אינו מוכיח דבר על base/large |
| [SimpleHTR ONNX](https://huggingface.co/PellelNitram/xournalpp-htr-simple-htr) | מילה בכתב יד לטיני וספרות | MIT, CPU; נבדק ונפסל לדוגמה הנוכחית |
| [PARSeq](https://github.com/baudm/parseq) | זיהוי רצפי טקסט; בסיס אפשרי להתאמה לספרות | Apache-2.0; משקולות זמינות. צריך לבדוק גרסה וקדם־עיבוד, לא נבדק |
| [Florence-2 base-ft](https://huggingface.co/microsoft/Florence-2-base-ft) | כ־231M פרמטרים, OCR/מיקום; עברית לא אומתה | MIT; לא נבדק כאן |
| [Granite Docling 258M](https://huggingface.co/ibm-granite/granite-docling-258M) | חילוץ מבנה מסמך; כרטיס אנגלית | Apache-2.0; אינו תחליף עברית מאומת |
| [SmolVLM2 500M](https://huggingface.co/HuggingFaceTB/SmolVLM2-500M-Video-Instruct) | ראייה קטנה; לא הוכח יתרון OCR/עברית | Apache-2.0; לא נבדק כאן |
| [Moondream2](https://huggingface.co/vikhyatk/moondream2) | כ־1.9B; קריאת אזורים/הקשר, עברית לא אומתה | Apache-2.0 בכרטיס שנבדק; לא נבדק כאן |
| [GOT-OCR-2.0-hf](https://huggingface.co/stepfun-ai/GOT-OCR-2.0-hf) | כ־560M, OCR; עברית לא אומתה | Apache-2.0 בכרטיס המרה זה; יש לבדוק תנאי המשקולות והגרסה המדויקת לפני הפצה |
| [DeepSeek-OCR-2](https://huggingface.co/deepseek-ai/DeepSeek-OCR-2) | כ־3.4B, מועמד למחשב חזק יותר | Apache-2.0; דוגמאות GPU אינן הוכחת CPU/Windows; לא נבדק כאן |
| [olmOCR-2-7B](https://huggingface.co/allenai/olmOCR-2-7B-1025) | כ־8.3B בפועל, כרטיס אנגלית; כבד למחשב חלש | Apache-2.0; לא נבדק כאן, לא ברירת מחדל |
| [Nanonets-OCR2-3B](https://huggingface.co/nanonets/Nanonets-OCR2-3B) | כ־3.75B, עברית לא אומתה | רישיון לא צוין במטא־נתונים שנבדקו; אין להסיק רישיון מבסיס Qwen |
| [HunyuanOCR](https://huggingface.co/tencent/HunyuanOCR) | כ־1.12B, OCR; עברית לא אומתה | רישיון Tencent Hunyuan Community, **אינו MIT/Apache**; מגבלות ותנאים מסחריים/אזוריים |
| [Churro-3B](https://huggingface.co/stanford-oval/churro-3B) | כתב יד היסטורי רב־לשוני, כולל מחקר עברית; כ־3.75B | **Qwen Research**, אינו מסלול ברירת מחדל לתוכנה מסחרית |
| [dots.ocr / dots.mocr](https://github.com/studio-dots-ai/dots.ocr) | חילוץ מסמכים רב־לשוני; עברית לא אומתה | משקולות וקוד זמינים; יש לקרוא גם הסכם משקולות ולא רק LICENSE של הקוד. 1.5 שונה לשם dots.mocr |
| [EasyOCR](https://github.com/JaidedAI/EasyOCR/blob/master/easyocr/config.py) | רשימת המודלים הרשמית שנבדקה **אינה כוללת עברית** | Apache-2.0; לא חלופה לעברית כברירת מחדל, אפשר לבדוק ספרות בנפרד |

## בחירה מעשית למחשב חלש

להתחיל ב־Tesseract Hebrew לדפוס ולהשוות גם את heb_rivok הקטן וב־PP-OCRv5/v6 לחיתוכי ספרות. לבדוק Rivok לדפוס מול Indra/Tesseract, בלי להניח שהוא טוב יותר: בניסוי שלנו גם הוא שגה. כחלופת ראייה לספרות, GLM-OCR ו־Qwen3.5-0.8B פעלו בפחות זיכרון מ־Qwen3-VL-2B בתנאי הניסוי, אך אף אחד לא פתר את כל המקרים. להפעיל מנוע ראייה אחד בכל פעם. 8GB/16GB הן נקודות תכנון בלבד עד בדיקה על מחשב Windows המיועד.

הורדת המועמדים שנבדקו, בגרסאות מקובעות ועם SHA-256:

```bat
.venv\Scripts\python scripts\setup_vision.py --profile glm-ocr
.venv\Scripts\python scripts\run_vision.py --profile glm-ocr
```

אפשרויות נוספות: `qwen35-08`, `paddle-vl16`, `lighton`, `digits` (Qwen3-VL-2B), `multilingual` (Qwen3-VL-4B). בפרופילים החדשים בוחרים במערכת ״מודל מקומי · ספרות בלבד״ לפעולת צ׳ק ומזינים את שם הפרופיל כשם המודל. הם מאזינים ב־127.0.0.1:8090 ומוגבלים להצעות ספרות; אין אישור אוטומטי. לא צריך להוריד את כולם לשימוש יומיומי.

## מה צפוי לשפר יותר מעוד מודל

שמירת החיתוך המקורי, השוואת קדם־עיבוד ללא מחיקה אוטומטית של קווים, הצגת מחלוקות ותיקון ידני. מאגר דוגמאות מתויגות מאפשר התאמה לכתב המסוים; שני ערכי אמת מאפשרים ניסוי התאמה קטן אך אינם מספיקים למדידת איכות על מסמכים חדשים. מפצלים לאימון/בדיקה לפי מסמך וכותב, ושומרים תאריכים/מסמכים שלא נראו באימון. מודדים רצף צ׳ק שלם כולל אפסים מובילים, סכומים ומסמך שלם בנפרד. הסכמה בין מנועים אינה הוכחת נכונות.

בגרסה הנוכחית כבר מומש אימון CTC אישי מתוך המסך: מתאם חזותי קטן מעל מנוע הרצפים, עם משקולות פרטיות, אימון CPU ב־NumPy, דוגמאות מכמה כותבים וקבוצת בדיקה נפרדת מהאימון. תיקוני מלל וספקים מלמדים התאמות ותבניות; הם אינם מאמנים את מודל הספרות לקרוא עברית. ניתן להעביר את הלמידה בין מחשבים ועובדים במפורש, עם תצוגה מקדימה ובדיקה מחדש של גרסאות מיובאות. ראו [מדריך הלמידה האישית](PERSONAL_LEARNING.md).

להורדת מודלי Tesseract העבריים הנוספים בלבד: `python scripts/setup_tesseract_rivok.py`. הם נשמרים בנפרד ואינם מחליפים את ברירת המחדל; הוראות השוואה מודפסות בסיום. המדידה נעשתה על הגרסאות המכווצות שב־PyPI 1.0.1, לאחר אימות SHA-256; אותה זהות קבצים מקובעת בסקריפט.

כדי למדוד במחשב המיועד על תיקיית חיתוכי ספרות: `python scripts/benchmark_digits.py PATH_TO_CROPS --engine paddle-v6-small`. לפרופיל ראייה מפעילים קודם את `run_vision.py` המתאים ומעבירים את שמו ב־`--engine`. פלט פרטי נשמר תחת `data/model-benchmarks`; אפשר להעביר `--truth` עם JSON הממפה שם תמונה לרצף ספרות כמחרוזת. ללא תיוג אין חישוב דיוק, ונתוני החימום, סוג החיתוכים וכמות הדוגמאות משפיעים על התוצאה.
