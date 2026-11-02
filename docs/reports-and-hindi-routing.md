# Three fixes: spoken Hindi, both OTP channels, and real report formats

16 Oct 2026.

## The conversational bot answered the wrong thing

Asked, in Hindi, *"which crops should I grow in my fields?"*, Saarthi replied *"how is
your wheat doing?"* — small talk about a stored field instead of an answer.

**Root cause: the intent router had no Devanagari.** `FARM_LEXICON` and every keyword
were Latin-script. Romanised Hinglish typed on the dashboard (`kaun si fasal ugaye`)
scored 0.95; the same question in Devanagari scored nothing, fell through to `general`,
and `general` is not in `DATA_INTENTS` — so the farm data and Mistral were never
consulted and Sarvam made conversation instead.

The phone line transcribes spoken Hindi **into Devanagari**. So every spoken Hindi
question had been taking the small-talk path, while the typed Hinglish path that
everyone tested worked perfectly. That is why it went unnoticed.

Fixed by giving the lexicon a Devanagari half. It is keyed on **stems**, because Hindi
inflects — खेत becomes खेतों, फसल becomes फसलें, उगा becomes उगाने — and a whole-word
table would need every form. `normalise_query` already tokenised Devanagari; only the
vocabulary was missing.

A few native phrases went onto the intents themselves where a single word is genuinely
ambiguous: मौसम means both *weather* and *season*, so "इस मौसम में कौन सी फसल लगाऊं"
scored as a weather question until `कौन सी फसल` disambiguated it.

Two smaller contributors fixed at the same time:

* The memory prompt ended *"Use it: greet them by name, and talk about their crop and
  their field"* — an instruction to steer every reply back to the stored field. It now
  says to use the details **where relevant to what was asked**, and not to change the
  subject.
* The persona now opens with **answer the question they asked**.

### Answers got room to be answers

The 14-word cap exists for the two-second budget, but it cut *"...लेकिन अगर आप अगली
फसल."* — a sentence that stops before the crops. A reply that is **answering a
question** may now run to 20 words (`VOICE_MAX_WORDS_DATA`); small talk stays at 14.
That costs about 0.6 s of speech, putting a data turn near 2.5 s. A farmer will wait
half a second for crop names and will not thank us for a fast non-answer.

Verified live: *"मुझे यह जानना है कि खेतों में कौन सी कौन सी फसल उगाने चाहिए?"* →
*"व्हीट तो आपने ही बोया है, लेकिन अब फरवरी में आप मोठ (मसूर), तिल, सरसों"* — real crop
names, `crop_selection`, answered by Mistral from the farm data.

## The code only ever reached one channel

`_deliver` picked email if the account had one, SMS otherwise — while the sign-in page
had always promised *"one 6-digit code to the email address and the phone number"*.
Anyone who signed up on the web therefore only ever got email.

And the SMS would not have worked anyway: the target was the bare ten-digit number,
and Twilio needs E.164.

Now **one** code is issued and sent to every channel the account has. One code, not one
per channel — issuing per channel would have cancelled the first, so whichever message
arrived second would be the only one that worked. A channel that fails is reported
rather than hidden: half-delivered is not delivered.

Verified live: `sent to: th**************@gmail.com and ********0988`.

## Reports: PDF and Excel, not just JSON

`GET /api/reports/{id}?format=json|pdf|xlsx`. JSON is unchanged, so anything already
reading the endpoint is unaffected.

**PDF** (`app/reports/pdf.py`, reportlab) is a document: green header rule and footer
on every page, a cover line naming the farm, metric tiles, charts, tables, and a
provenance callout at the top of each report. Charts are drawn with reportlab's own
primitives rather than matplotlib — one fewer heavy dependency, and they come out as
vectors, so they stay sharp in print.

Two judgements worth keeping:

* **Nutrients get a bar chart; everything else gets a range row.** N, P and K share a
  unit so bars compare them honestly. Putting 195 mg/kg of potassium on the same axis
  as a pH of 7.2 makes the pH invisible and implies a comparison that does not exist,
  so pH, EC, temperature and moisture are each drawn against their own typical band
  with a marker where this reading sits — amber when it falls outside.
* **Provenance is on the page, not in a footnote.** A simulated reading says SIMULATED
  in a callout; a farmer's typed figures say they are the farmer's.

**Excel** (`app/reports/excel.py`, openpyxl) gives what a CSV cannot: typed numbers
that sort, frozen headers, autofilter, fitted columns and a native chart. Provenance is
a **column**, because a sheet where "simulated" appears only in the title will be
quoted out of context. The call report gets a second sheet with every spoken turn, and
every workbook gets an About sheet naming the data sources.

The dashboard's report cards now carry **PDF / Excel / JSON** buttons.
