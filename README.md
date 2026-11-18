# In sala

Programmazione dei cinema che frequento, in un'unica pagina: **https://ivanzanolla.github.io/cinema/**

Cinema: The Space Silea, Cinergia Conegliano, Multisala Verdi Vittorio Veneto, La petite Lumière Belluno.

- `scraper/scrape.py` scarica film, orari e link d'acquisto da ogni sito e genera `site/data.json`
- `site/index.html` è la pagina (statica, legge `data.json`)
- `.github/workflows/update.yml` gira ogni giorno (circa 05:15 e 12:45) e pubblica su GitHub Pages; si lancia a mano da *Actions → Aggiorna programmazione → Run workflow*

Se un sito cambia struttura o blocca la richiesta, quel cinema mostra i dati dell'ultimo aggiornamento riuscito e il problema compare nel footer della pagina.

Per aggiungere un cinema: una funzione `scrape_xxx()` che restituisce film con `shows`, più una voce in `CINEMAS` e un colore `--xxx` nel CSS.
