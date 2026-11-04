# Getting started with Hal Employment

Hal Employment helps you find jobs and apply for them. It looks for jobs that suit you, explains why each
one fits, writes a CV and cover letter for each job you choose, and fills in the employer's form in your
browser. You check everything, and you press Submit on the employer's site yourself. Hal never presses
Submit, and it never sends anything to an employer.

This page is for anyone using Hal. You do not need to know anything technical.

## What you need

- A Windows 10 or 11 computer, or a 64-bit Linux desktop, with Hal installed:
  [on Windows](../howto/install-on-windows.md) or [on Linux](../howto/install-on-linux.md).
- An AI for Hal to write with: a local model, if your computer has a strong graphics card, or a Claude Pro
  or Max subscription. The setup guide helps you choose.
- Your CV, as a PDF or Word file, if you have one. If not, Hal can build one with you.
- About 30 minutes for the setup guide. You can stop at any time and carry on later.

## Open Hal

Open **Hal Employment** from the Start menu, the desktop or your applications menu. Hal opens in its own
window.

Closing the window does not stop Hal: it keeps looking for jobs in the background. Open it again from the
same shortcut. To pause or quit Hal, open **Settings**, **Advanced**.

If Hal ever shows "For your safety, Hal opens from its shortcut", close that page and open Hal from its
shortcut again.

## The setup guide

The first time you open Hal, the setup guide opens by itself. Each answer is saved as you type, and
**Save and finish later** is on every step. Steps you skip stay on Home under "Finish setting up".

1. **Welcome.** What Hal does, what it never does, and what stays on your computer.
2. **Your CV.** Choose one of three ways: use your CV as it is, let Hal suggest improvements, or build a
   fresh CV. Upload a PDF or Word file, then press **Check what Hal read** to see that Hal read it correctly.
3. **About you.** Your name, where you live, and the details forms ask for. Most of it is filled in from
   your CV ("From your CV"). The private questions (equality and adjustments) are optional; see
   [Your privacy](privacy.md).
4. **What you're looking for.** Your career stage, whether you are studying now, and the kinds of role you
   want: graduate jobs, graduate schemes, paid internships, placement years, insight weeks and spring
   weeks, apprenticeships, part-time or fixed-term work. Press every career area you are interested in, such
   as **Heritage and museums** and **Creative and media**; each fills in example roles you can change. Hal
   then says "Here is what I understood"; press **Change something** if it is not right.
5. **Your record.** Hal makes a folder for your certificates, references and other evidence. Press
   **Add files** to copy documents into it.
6. **How Hal thinks.** Choose **On this computer** if your computer runs a local model through Ollama, or
   **Your Claude subscription** (Claude Pro or Max). For Claude, press **Install**, then **Sign in**, then
   **Test**. Hal never sees your Claude password.
7. **Browser helper.** The helper lets Hal fill in forms in your own browser, where you are already signed
   in. Pick your browser and follow the steps on the screen, or see
   [Load the browser helper](../howto/load-the-browser-helper.md).
8. **Email** (optional). Lets Hal read replies from employers and job alerts from a Gmail inbox. Hal never
   sends email from your account, except your own daily summary to you.
9. **Phone alerts** (optional). Hal can tell you on your phone when something needs you, through the free
   ntfy app.
10. **Updates** (optional). Hal checks GitHub for new versions and installs one only when you press
    **Update Hal**.
11. **All set.** Hal starts its first search. Home shows how far it has got.

## A normal day with Hal

1. Open **Home**. It lists only what needs you today: new matches worth a look, applications waiting for
   you, replies from employers.
2. Open **Jobs**, **Matches**. Each job says why it fits you and anything that does not. Press **Save** to
   keep a job for later, or **Not for me** and say why, so Hal learns what you want.
3. On a job you like, press **Apply with Hal**. Hal writes the documents for this job: your CV adjusted
   for it, a cover letter, and answers to the form's questions. To choose several jobs first, press
   **Add to queue** on each instead (or Q): they wait under **Getting ready** on the **Applications**
   board while Hal writes, and **Approve all** there approves them together.
4. Read the documents. Change anything you like, or ask Hal for changes. When you are happy, press
   **Approve and fill in the form**.
5. Hal opens the employer's form in your browser and fills it in. Each answer is marked "Fine" or "Check
   this", and **Why this answer?** under each one says where it came from.
6. Check the form, then press **Submit** on the employer's site yourself. The application moves to
   **Sent** on the **Applications** board.
7. When the employer replies, Hal sorts it into **Messages** and moves the application on the board.

### When Hal cannot fill in a form

Some sites ask for an account, a test or a form Hal cannot fill in. Open **Documents** and pick the
application: the CV, letter and answers you approved are there, with **Download**, **Copy all** and a
**Copy-paste details** tab for your name, email and similar boxes. Send the application on the employer's
site, then press **I sent it myself**, so Hal moves it to Sent and watches your email for a reply.

## Where Hal looks

Hal searches a list of UK job sites chosen to suit your goal, from universities and charities to the
public sector and technology. **Jobs**, **Where Hal looks** lists each one, with a switch.

- **Add a job site or careers page:** paste the address of an employer's "Jobs" page. Hal reads pages on
  Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee and Personio, pages that link a job feed,
  and pages that carry the job details search engines read. For any other page, add each job with
  **Add one advert**.
- **Watch a company:** type its name, and Hal looks for its job board on the systems above.
- **Free keys:** Adzuna and Reed need a free key each. Hal works without them and says what it skips; press
  **Get a free key** on the site's row to add one.
- **Email alerts:** many UK student and graduate sites send job alerts by email (RateMyPlacement, Bright
  Network, Prospects, TARGETjobs, Milkround, Gradcracker, CharityJob, Civil Service Jobs, NHS Jobs,
  jobs.ac.uk and others). Sign up with the address Hal reads, and each job in an alert becomes a match.
  This is the best way to hear about internships, placement years and insight weeks.

## Where things are

| In the menu | What it is for |
| --- | --- |
| Home | What needs you today |
| Jobs | Matches, Saved and Hidden jobs, and Where Hal looks |
| Applications | Every application, from Getting ready to Form filled, Sent, Interview and Offer |
| Messages | Replies from employers and job alerts, sorted by Hal |
| My CV | Your CV, Hal's suggestions, its type and look, checks, and every version |
| Documents | The CVs and letters you approved, by application, ready to copy or send |
| Your record | Your evidence: certificates, references, past work, and what Hal learned from them |
| Activity | Everything Hal did and why, with Undo where it can be undone |
| What Hal remembers | Suggestions from your choices, what Hal learned, saved answers, notes about job sites |
| Settings | Every choice, grouped as in the setup guide, plus Is Hal working? and Updates |

Press **Ctrl K** anywhere to search or jump to a page, and **Help and shortcuts** for the keys.

## What Hal remembers

- **Waiting for you**: suggestions from your choices, for example a smaller travel distance after you hide
  several jobs as "Too far". Nothing changes until you press **Yes, do this**. **Not now** asks again in a
  week; **Never suggest this** stops that suggestion for good.
- **Learned**: what you said yes to, where it came from and when. Switch one off or delete it to put things
  back as they were.
- **Notes about job sites**: what works on each site, and where each note came from. Switch off any you do
  not want.
- **Saved answers**: answers Hal reuses on forms. Each says where Hal may use it; one marked **Choose where
  to use it** is never used until you pick.

To stop the suggestions, open **Settings**, **Advanced**, **Suggestions from your choices**.

## When something goes wrong

1. Open **Settings**, **Is Hal working?**. Each line is fine or says what to do, often with a button that
   fixes it.
2. If a form went wrong on a job site, press **Something wrong here?** on the application's page and say
   what happened in your own words. Hal remembers it for that site.
3. If you are still stuck, open an issue on the project's GitHub page. **Make a help file** on the
   **Is Hal working?** page makes a file you can attach: your name, contact details and answers are taken
   out, and you read it before it is saved.

| Symptom | Fix |
| --- | --- |
| "For your safety, Hal opens from its shortcut" | Close the page and open Hal from its shortcut. |
| Hal does not open at all | Restart the computer and open Hal again. If it still does not open, update it from outside the app ([Update Hal Employment](../howto/update-hal.md)). |
| No new jobs for days | Open **Jobs**, **Where Hal looks** and check the sites are switched on, then **Settings**, **What you're looking for**. |
| "Hal cannot read this page by itself yet" | The careers page has no job details Hal can read. Use **Add one advert** for each job on it. |
| Hal cannot write documents | **Settings**, **Is Hal working?** shows the AI line. With Claude, press **Sign in to Claude again** if it asks. |
| The form does not fill in | Check the browser helper is connected (**Settings**, **Is Hal working?**), then try **Something wrong here?** on the page. |
