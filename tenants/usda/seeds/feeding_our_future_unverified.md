# Feeding Our Future: names left out of the entity CSV

This file goes with `feeding_our_future_entities.csv`. It lists names that were left out of the CSV, or that need a caveat, and gives the reason for each.

## Placeholders and unnamed entities
- **"Sponsor A"**: DOJ releases and the Farah superseding indictment use this placeholder for a second sponsor. News reporting widely identifies it as Partners in Nutrition. DOJ never maps the two in the text, so "Sponsor A" stays in the Sponsor column exactly as DOJ wrote it. Partners in Nutrition is a CSV row only because the releases name it directly (Aug 4, 2025; Feb 7, 2025; Aug 6, 2025).
- **Unnamed shell companies**: the Jama/Brava releases mention five or six of them, Salim Said is said to have registered several, and the Eidleh companies appear in the Noor release. None are named.
- **Unnamed sites**: the Waite Park strip-mall market (Hamdi Omar), the Shakopee site, the Osseo and Minneapolis sites (Awad), the churches (Dorothy Moore), the Kenyan real estate company and "sham corporate entities" (Kenyan national release, Sep 4, 2025), and "Company 1".
- **"Empire" group**: this is how DOJ labels the group of people around Abdiaziz Farah, not a legal entity. Empire Cuisine and Market LLC and Empire Enterprises LLC are in the CSV.

## Named on justice.gov but deliberately excluded (third parties, not scheme entities)
- **Banks and credit unions holding seized accounts**: U.S. Bank, JP Morgan Chase, 1st United Bank, TruStone Financial CU, Spire CU, Wells Fargo, Bell Bank, Northeast Bank, Star Choice CU, Stock Yards Bank.
- **Merchants and payees for proceeds**: Porsche Minneapolis, Lupient, Dodge of Burnsville, Morrie's 394 Hyundai, Wings Financial CU, Pulte Homes, Trademark Title Services, Johnson Reiland Builders & Remodelers, 3 Pillar Homes (Ohio), Farhia Jewelry (Dubai), Coinbase, Burberry, Louis Vuitton, Canada Goose, Minnesota Timberwolves, GoFundMe, Herzing University, PayMeToDoYourHomework.com, Hallmark. These are payees only. Johnson Reiland, 1st United Bank and Trademark Title could still matter for tracing funds.
- **Venues and public bodies named as locations**: Midtown Global Market (the site location for Safari Express), JigJiga Business Center (the building housing Community Enhancement Services), Lazy U mobile home park (Medford) and Vista Villa mobile home park (Waseca), which were proposed site locations in the indictment, and Rochester, Shakopee, Bloomington, Medford, St. Cloud and Faribault public schools. These are real third-party places, not participants.
- **Medicaid-fraud entities in mixed releases**: A-Z Friendly Languages, Itasca Interpretation Services and Minnesota Multicultural Counseling and Consultant (Awad plea, Nov 4, 2022), plus Star Autism Center, Pristine Health, SafeLodgings, Foundation First and Ultimate Home Health (Dec 18, 2025 release). These relate to Medicaid/HSS/EIDBI fraud, not the child nutrition program.

## Known names that could not be tied to a justice.gov page in this pass
Each of these is said in news reports to appear in court filings. The justice.gov pages fetched for this pass did not show them, so they were left out of the CSV:
- Entities in the other Sept 2022 indictments (the Salim Said/Safari, S&S Catering, Haji's Kitchen, Brava, Bock and Ikram Mohamed cases). Only the Farah et al. superseding indictment PDF turned up as a justice.gov attachment. Those indictments probably name many more sites and shell companies. They are on PACER/CourtListener (for example gov.uscourts.mnd.203016), but court records were not used for CSV rows.
- Entities tied to Abdikerm Eidleh, Ikram Sharif Mohamed (beyond IM Consultation / Star Distribution), Abdiwahab Aftin, and Mulata Yusuf Ali. Mulata Ali was charged on Mar 13, 2023 with no entity named.

## Caveats on the data
- Source quotes were pulled through a page-summarizing fetch tool and requested verbatim. Curly apostrophes and quotes follow the page text as the tool returned it. Spot-check a quote before using it as legal citation text.
- **Brava Cafe** (Minneapolis, Hanna Marekegn) and **Brava Restaurant & Café LLC** (Rochester, the Jama family) are different entities.
- The **S & S Catering** owner differs between releases: the Sept 2022 release names Qamar Ahmed Hassan, and the May 2025 sentencing names Sahra Nur. Both are listed.
- Some DOJ releases contain typos: "April 9, 2016" for 2026 in the Star Distribution/GAK release, and the Bloomington-woman release (dated Oct 17, 2024) refers to a Nov 4, 2024 trial. Status dates in the CSV follow the release date.
- Defendant names in the Status column mean what the release says about the people. No entity was itself charged as a corporate defendant in any release reviewed.
