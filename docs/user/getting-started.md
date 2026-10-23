# Getting started with Hal Employment

Hal Employment helps you find jobs and apply for them. It looks for jobs that suit you, explains why each
one fits, writes a CV and cover letter for each job you choose, and fills in the employer's form in your
browser. You check everything, and you press Submit on the employer's site yourself. Hal never presses
Submit, and it never sends anything to an employer.

This page is for anyone using Hal. You do not need to know anything technical.

## What you need

- Hal installed on your computer. If it is not yet, ask the person who set Hal up for the install file,
  then follow [Install Hal Employment on Windows](../howto/install-on-windows.md) or
  [Install Hal Employment on Linux](../howto/install-on-linux.md).
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
   weeks, apprenticeships, part-time or fixed-term work. Hal then says "Here is what I understood"; press
   **Change something** if it is not right.
5. **Your record.** Hal makes a folder for your certificates, references and other evidence. Press
   **Add files** to copy documents into it.
6. **How Hal thinks.** Hal needs an AI to write. Choose **Your Claude subscription** (Claude Pro or Max) or
   **On this computer** if your computer has a strong graphics card. For Claude, press **Install**, then
   **Sign in**, then **Test**. Hal never sees your Claude password.
7. **Browser helper.** The helper lets Hal fill in forms in your own browser, where you are already signed
   in. Pick your browser and follow the four steps on the screen. More help:
   [Load the browser helper](../howto/load-the-browser-helper.md).
8. **Email** (optional). Lets Hal read replies from employers and job alerts. Hal never sends email from
   your account, except your own daily summary to you.
9. **Phone alerts** (optional). Hal can tell you on your phone when something needs you, through the free
   ntfy app.
10. **Updates** (optional). Hal updates itself from GitHub when you press **Update Hal**.
11. **All set.** Hal starts its first search. Home shows how far it has got.

## A normal day with Hal

1. Open **Home**. It lists only what needs you today: new matches worth a look, applications waiting for
   you, replies from employers.
2. Open **Jobs**, **Matches**. Each job says why it fits you and anything that does not. Press **Save** to
   keep a job for later, or hide it and say why, so Hal learns what you want.
3. On a job you like, press **Apply with Hal**. Hal writes the documents for this job: your CV adjusted
   for it, a cover letter, and answers to the form's questions.
4. Read the documents. Change anything you like, or ask Hal for changes. When you are happy, press
   **Approve and fill in the form**. The first time, Hal shows a short guide.
5. Hal opens the employer's form in your browser and fills it in. Green answers are ones Hal is sure of;
   amber ones need you to check them. **Why this answer?** under each one says where it came from.
6. Check the form, then press **Submit** on the employer's site yourself. The application moves to
   **Sent** on the **Applications** board.
7. When the employer replies, Hal sorts it into **Messages** and moves the application on the board.

If a site asks for an account or a test Hal cannot do, Home says so, and you can
[send the application yourself](../howto/send-an-application-yourself.md) with the documents Hal made.

## Where things are

| In the menu | What it is for |
| --- | --- |
| Home | What needs you today |
| Jobs | Matches, Saved and Hidden jobs, and Where Hal looks (the job sites Hal searches) |
| Applications | Every application, from Getting ready to Form filled, Sent, Interview and Offer |
| Messages | Replies from employers and job alerts, sorted by Hal |
| My CV | Your CV, Hal's suggestions, its type and look, checks, and every version |
| Documents | The CVs and letters you approved, by application, ready to copy or send |
| Your record | Your evidence: certificates, references, past work, and what Hal learned from them |
| Activity | Everything Hal did and why, with Undo where it can be undone |
| What Hal remembers | Suggestions Hal has from your choices, things it learned, saved answers, notes about job sites |
| Settings | Every choice, grouped as in the setup guide, plus Is Hal working? and Updates |

Press **Ctrl K** anywhere to search or jump to a page, and **Help and shortcuts** for the keys.

## What Hal remembers

- **Waiting for you**: suggestions Hal has from your choices, for example a smaller travel distance after you
  hide several jobs as "Too far". Nothing changes until you press **Yes, do this**. **Not now** asks again in a
  week; **Never suggest this** stops that suggestion for good.
- **Learned**: what you said yes to, where it came from and when. Switch one off or delete it to put things
  back as they were.
- **Notes about job sites**: what works on each site. Each note says where it came from; switch off any you
  do not want, and share one with whoever looks after Hal.
- **Saved answers**: answers Hal reuses on forms. Each says where Hal may use it; one marked **Choose where
  to use it** is never used until you pick.

To stop the suggestions, open **Settings**, **Advanced**, **Suggestions from your choices**.

## When something goes wrong

1. Open **Settings**, **Is Hal working?**. Each line is fine or says what to do, often with a button that
   fixes it.
2. If a form went wrong on a job site, press **Something wrong here?** on the application's page and say
   what happened in your own words. Hal remembers it for that site.
3. If you are still stuck, press **Make a help file** on the same page and send it to the person who set
   Hal up. You see the file before it is saved, and your name, contact details and answers are taken out.

| Symptom | Fix |
| --- | --- |
| "For your safety, Hal opens from its shortcut" | Close the page and open Hal from its shortcut. |
| Hal does not open at all | Restart the computer and open Hal again. If it still does not open, update from the Start menu: **Update Hal Employment** ([Update Hal Employment](../howto/update-hal.md)). |
| No new jobs for days | Open **Jobs**, **Where Hal looks** and check the sites are switched on, then **Settings**, **What you're looking for**. |
| Hal cannot write documents | **Settings**, **Is Hal working?** shows the AI line. With Claude, press **Sign in to Claude again** if it asks. |
| The form does not fill in | Check the browser helper is connected (**Settings**, **Is Hal working?**), then try **Something wrong here?** on the page. |
