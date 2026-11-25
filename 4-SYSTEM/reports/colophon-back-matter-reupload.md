# Colophon back_matter: re-upload list

2026-11-25. Every block under a མཇུག་བྱང heading that is not a verse stanza is `back_matter`
(parser-root-text, `BACK_MATTER_HEADINGS`). Translations take their root segment's type.
Live, most colophons are `verse` (the old document default), so each text below needs:

1. root: `library.py text replace <note> --env <env> --dev --map --yes` (prod: `--confirm prod`) — re-segmentation, new segment ids
2. its translations: `renumber_to_root.py <translation notes> --env <env> --map --yes` — retypes the colophon segment
3. prod: studio id-remap of the new segment id maps

Cells: root state; translations with a segment to retype / aligned translations. Texts marked *in the 13* get the fix with their re-segmentation.

**On hold (2026-11-25): texts shared with `Taklung_Monlam_Chenmo_Rails`.** That vault's notes claim the
same text/edition ids and it has uploaded its own versions over ours on prod/testing. Not updated
from here until ownership is settled: ཐབས་མཁས་ཐུགས་རྗེ་མ, བྱང་ཆུབ་སེམས་དཔའི་ལྟུང་བ་བཤགས་པ,
འཕགས་པ་བཟང་པོ་སྤྱོད་པའི་སྨོན་ལམ, སྤྱོད་འཇུག་སྨོན་ལམ (on this list), and also བསྟན་འབར་མ, ཡན་ལག་བདུན་པ,
བྱམས་པའི་སྨོན་ལམ, འཕགས་པ་དཀོན་མཆོག་གསུམ་རྗེས་སུ་དྲན་པའི་མདོ.

| # | Text | dev | testing | prod |
|---|------|-----|---------|------|
| 1 | ཀརྨ་པའི་གསོལ་འདེབས་རྒྱལ་ཀུན་ཕྲིན་ལས་གཟུགས་ཅན་མ་བཞུགས་སོ། ། | ok; 0/0 tr | ok; 0/0 tr | ok; 0/0 tr |
| 2 | ཀླུ་བསངས་ནོར་བུ་དགོས་འདོད་ཀུན་འབྱུང་ཞེས་བྱ་བ་བཞུགས་སོ། ། | retype; 0/0 tr | retype; 0/0 tr | retype; 0/0 tr |
| 3 | གཏོར་མ་ཆ་གསུམ་བཞུགས་སོ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 4 | གནས་བཅུའི་བསྟོད་པ་བཞུགས་སོ། ། | ok; 0/0 tr | ok; 0/0 tr | ok; 0/0 tr |
| 5 | ཆགས་མེད་བདེ་སྨོན། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 6 | ཆོ་འཕྲུལ་གྱི་བསྟོད་པ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 7 | ཇ་མཆོད་གཙུག་གི་ནོར་བུ་བཞུགས་སོ། ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 8 | ཏཱ་ཡི་སི་ཏུ་རིམ་པར་བྱོན་པ་རྣམས་ཀྱི་གསོལ་འདེབས། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 9 | ཐབས་མཁས་ཐུགས་རྗེ་མ་བཞུགས་སོ། | in the 13 (resegment); 6/6 tr | in the 13 (resegment); 0/6 tr | in the 13 (resegment); 0/6 tr |
| 10 | ཐུབ་པའི་བསྟོད་པ་གང་ཚེ་རྐང་གཉིས་མ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 11 | ཐུབ་པའི་བསྟོད་པ་ཐུབ་རྣམས་སྤངས་རྟོགས། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 12 | ཐུབ་པའི་མཛད་པ་བཅུ་གཉིས་ལ་བསྟོད་པ་ཞེས་བྱ་བ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 13 | ཐོག་མཐའ་མ་བཞུགས་སོ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 14 | དགོངས་གཅིག་ལྷན་ཐབས་བཞུགས་སོ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 15 | དཔལ་གསང་བ་འདུས་པའི་བསྒོམ་བཟླས་རྒྱུན་འཁྱེར་བཞུགས་སོ། ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 16 | དཔལ་ནཱ་ལེནྡྲའི་པཎ་གྲུབ་བཅུ་བདུན་གྱི་གསོལ་འདེབས། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 17 | དཔལ་ལྡན་ས་གསུམ་མ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 18 | དཔལ་ས་སྐྱ་པའི་བསྟན་པ་དར་ཞིང་རྒྱས་པའི་སྨོན་ལམ་འཇམ་དབྱངས་བླ་མ་དགྱེས་པའི་ཞལ་ལུང་ཞེས་བྱ་བ་བཞུགས་སོ། ། | in the 13; root ok; 0/6 tr | in the 13; root ok; 6/6 tr | in the 13; root ok; 6/6 tr |
| 19 | དབང་སྡུད་གསོལ་འདེབས། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 20 | དབྱངས་ཅན་མའི་བསྟོད་པ་བཞུགས་སོ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 21 | དུས་མཁྱེན་གྱི་རྣམ་ཐར་བསྟོད་པ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 22 | པདྨ་མཁའ་འགྲོའི་རིགས་བྱེད་རྩལ་གྱི་བསང་མཆོད་པདྨའི་དྲྭ་བ་བཞུགས་སོ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 23 | ཕ་དམ་པ་སངས་རྒྱས་ཀྱི་ཞལ་གདམས་དིང་རི་བརྒྱ་རྩ་མ་བཞུགས་སོ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 24 | ཕྱག་ཆེན་བརྒྱུད་པའི་གསོལ་འདེབས་ནི། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 25 | བཀའ་ཐང་བསྡུས་པ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 26 | བདུད་བཟློག་གསང་བའི་མན་ངག | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 27 | བདེ་སྨོན་བསྡུས་པ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 28 | བསྔོ་བ་དང་སྨོན་ལམ་བསྡུས་པ་བཞུགས་སོ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 29 | བོད་ཡུལ་བདེ་བའི་སྨོན་ལམ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 30 | བོད་སྐྱོང་ལྷ་སྲུང་གི་འཕྲིན་བསྐུལ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 31 | བྱང་ཆུབ་སེམས་དཔའི་ལྟུང་བ་བཤགས་པ། | in the 13; root ok; 6/6 tr | in the 13 (resegment); 0/6 tr | in the 13 (resegment); 0/6 tr |
| 32 | བྱམས་པའི་སྐུ་གཟུགས་མ་བཞུགས་སོ།། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 33 | བྱིས་པའི་གདོན་ཆེན་བཅོ་ལྔ་ཞི་བར་བྱེད་པའི་གཟུངས་བཞུགས་སོ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 34 | བྱིས་པའི་འབུམ་ཆུང་བཞུགས་སོ།། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 35 | བླ་མ་རྒྱང་འབོད་མོས་གུས་སྙིང་གི་གཟེར་འདེབས་བཞུགས་སོ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 36 | བློ་སྦྱོང་ཚིག་བརྒྱད་མ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 37 | མཁས་གྲུབ་རྗེའི་བསྟོད་པ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 38 | མགོན་པོ་ཕྱག་དྲུག་པའི་བསྟོད་པ་མྱུར་མཛད་མ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 39 | ཚེ་རབས་རྗེས་འཛིན་བཞུགས་སོ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 40 | ཚེས་བཅུའི་ཕན་ཡོན་གསོལ་འདེབས། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 41 | ཞབས་བརྟན་གསོལ་འདེབས་འཆི་མེད་གྲུབ་པ་ཞེས་བྱ་བ་བཞུགས་སོ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 42 | ཞབས་བརྟན་ནུབ་ཕྱོགས་བདེ་ལྡན་མ་བཞུགས་སོ།། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 43 | འཆི་ཁར་ཕན་པའི་གདམས་ངག་ཉམས་ལེན་གྱི་སྙིང་པོ་ཞེས་བྱ་བ་བཞུགས་སོ།། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 44 | འཇམ་དཔལ་རྫོགས་པ་ཆེན་པོའི་སྨོན་ལམ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 45 | འཕགས་པ་བཟང་པོ་སྤྱོད་པའི་སྨོན་ལམ་གྱི་རྒྱལ་པོ། | in the 13 (resegment); 6/6 tr | in the 13 (resegment); 1/6 tr | in the 13 (resegment); 1/6 tr |
| 46 | རི་བོ་བསངས་མཆོད་བཞུགས་སོ༔ | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 47 | རྒྱ་ནག་པོའི་སྐག་ཟློག་ཅེས་བྱ་བ་བཞུགས་སོ།། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 48 | རྒྱལ་ཁམས་བདེ་བའི་སྨོན་ལམ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 49 | རྒྱལ་བའི་དབང་པོ་ཀློང་ཆེན་རབ་འབྱམས་ལ་བསྟོད་པ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 50 | རྗེ་བཙུན་སྒྲོལ་དཀར་གྱི་བསྟོད་པ་མཁྱེན་བརྩེ་དྲི་མེད་མ། | ok; 0/0 tr | ok; 0/0 tr | ok; 0/0 tr |
| 51 | རླུང་རྟ་ཡར་བསྐྱེད་བསངས་མཆོད་བཞུགས་སོ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 52 | ལམ་གཙོ་རྣམ་གསུམ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 53 | ལམ་རིམ་སྨོན་ལམ་བཞུགས་སོ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 54 | སྒྲོལ་བསྟོད་སྐྱབས་བདུན་མ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 55 | སྒྲོལ་མ་ཙིཏྟཱ་མ་ཎི་ལ་བརྟེན་པའི་ཐུན་མོང་མ་ཡིན་པའི་བླ་མའི་རྣལ་འབྱོར་ཐར་པར་བགྲོད་པའི་ཐེམ་སྐས་ཞེས་བྱ་བ་བཞུགས་སོ། ། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 56 | སྤྱོད་འཇུག་སྨོན་ལམ་བཞུགས་སོ། | in the 13 (resegment); 6/6 tr | in the 13 (resegment); 6/6 tr | in the 13 (resegment); 6/6 tr |
| 57 | སྨོན་ལམ་རྡོ་རྗེའི་རྒྱ་མདུད་ནི། | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |
| 58 | ཨོ་རྒྱན་རིན་པོ་ཆེའི་ཞལ་ཆེམས་གསོལ་འདེབས་བཞུགས༔ | retype; 6/6 tr | retype; 6/6 tr | retype; 6/6 tr |

Totals:
- dev: 53 roots to retype, 318 translations (1 segment each, the colophon)
- testing: 52 roots to retype, 307 translations (1 segment each, the colophon)
- prod: 52 roots to retype, 307 translations (1 segment each, the colophon)

Done so far: text 2 (དཔལ་ས་སྐྱ…ཞལ་ལུང) on dev, testing and prod — root and 6 translations
(its row above was counted after dev; testing/prod translations are now done too).
