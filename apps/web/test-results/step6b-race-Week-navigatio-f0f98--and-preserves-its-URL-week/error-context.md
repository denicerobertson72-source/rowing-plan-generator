# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: step6b-race.spec.ts >> Week navigation advances within the saved PlanVersion and preserves its URL week
- Location: e2e/step6b-race.spec.ts:56:5

# Error details

```
Test timeout of 30000ms exceeded.
```

```
Error: locator.click: Test timeout of 30000ms exceeded.
Call log:
  - waiting for getByText('Next week')
    - locator resolved to <button disabled class="quiet">Next week →</button>
  - attempting click action
    2 × waiting for element to be visible, enabled and stable
      - element is not enabled
    - retrying click action
    - waiting 20ms
    2 × waiting for element to be visible, enabled and stable
      - element is not enabled
    - retrying click action
      - waiting 100ms
    58 × waiting for element to be visible, enabled and stable
       - element is not enabled
     - retrying click action
       - waiting 500ms

```

# Page snapshot

```yaml
- generic [active] [ref=e1]:
  - main [ref=e2]:
    - generic [ref=e3]:
      - paragraph [ref=e4]: ROWING PLAN GENERATOR
      - heading "Week" [level=1] [ref=e5]
      - paragraph [ref=e6]: Plan available online
    - generic [ref=e7]:
      - button "← Previous week" [disabled] [ref=e8] [cursor=pointer]
      - generic [ref=e9]: Plan week
      - button "Next week →" [disabled] [ref=e10] [cursor=pointer]
    - link "Adjust this week" [ref=e12] [cursor=pointer]:
      - /url: /weekly-override
    - paragraph [ref=e13]: No plan week is available.
    - navigation "Primary navigation" [ref=e14]:
      - link "Today" [ref=e15] [cursor=pointer]:
        - /url: /
      - link "Week" [ref=e16] [cursor=pointer]:
        - /url: /week
      - link "Season" [ref=e17] [cursor=pointer]:
        - /url: /season
      - link "Profile" [ref=e18] [cursor=pointer]:
        - /url: /profile
      - link "Account" [ref=e19] [cursor=pointer]:
        - /url: /account
  - button "Open Next.js Dev Tools" [ref=e25] [cursor=pointer]
  - alert [ref=e29]
```

# Test source

```ts
  1   | import { test, expect } from "@playwright/test";
  2   | 
  3   | test("Step 6B race draft create, failure guard, duplicate guard, and plan invalidation", async ({page}) => {
  4   |   let writes=0;
  5   |   page.on("request", request => { if (request.method()==="PUT" && request.url().includes("/athletes/")) writes++; });
  6   |   await page.goto("/profile");
  7   |   await expect(page.getByText("Synthetic Step 6B Rower")).toBeVisible();
  8   |   const initial=page.locator(".race-editor");
  9   |   await expect(initial).toHaveCount(3);
  10  |   await page.getByRole("button",{name:"Add race"}).click();
  11  |   await page.getByLabel("Race name").fill("Synthetic Oct 5");
  12  |   await page.getByLabel("Start date").fill("2026-10-05");
  13  |   await page.getByLabel("Priority").selectOption("B");
  14  |   await expect(page.getByRole("dialog")).toBeVisible();
  15  |   await expect(initial).toHaveCount(3);
  16  |   await page.getByRole("button",{name:"Save race"}).dblclick();
  17  |   await expect(page.getByRole("status")).toContainText("Race saved");
  18  |   await expect(page.getByRole("dialog")).toHaveCount(0);
  19  |   await expect(page.locator(".race-editor")).toHaveCount(4);
  20  |   expect(writes).toBe(1);
  21  |   const text=await page.locator(".race-editor").allTextContents();
  22  |   expect(text.join(" ")).toMatch(/Sep C[\s\S]*Oct 5[\s\S]*Oct B[\s\S]*Nov A/);
  23  |   await page.getByRole("button",{name:"Edit race"}).last().click();
  24  |   await page.getByLabel("Start date").fill("2026-09-15");
  25  |   await expect(page.locator(".race-editor").last()).toContainText("Synthetic Nov A");
  26  |   await page.getByRole("button",{name:"Save race"}).click();
  27  |   await expect(page.getByRole("status")).toContainText("Race saved");
  28  |   expect((await page.locator(".race-editor").first().innerText())).toContain("Synthetic Nov A");
  29  |   await page.getByRole("button",{name:"Edit race"}).first().click();
  30  |   await page.getByLabel("Race name").fill("Failure stays local");
  31  |   await page.route("**/api/v1/athletes/**", route => route.fulfill({status:500,body:"failure"}));
  32  |   await page.getByRole("button",{name:"Save race"}).click();
  33  |   await expect(page.getByRole("alert").filter({hasText:"Couldn’t save"})).toContainText("Couldn’t save");
  34  |   await expect(page.getByRole("dialog")).toBeVisible();
  35  |   await expect(page.getByLabel("Race name")).toHaveValue("Failure stays local");
  36  |   await page.unroute("**/api/v1/athletes/**");
  37  | });
  38  | 
  39  | test("Week presents semantic cards without internal role labels or narrow-screen overflow", async ({page}) => {
  40  |   await page.setViewportSize({width:375,height:812});
  41  |   await page.goto("/profile");
  42  |   await expect(page.getByText("Synthetic Step 6B Rower")).toBeVisible();
  43  |   await page.getByRole("button",{name:"Update plan with these choices"}).click();
  44  |   await expect(page.getByRole("status")).toContainText("Plan updated");
  45  |   await page.goto("/week");
  46  |   await expect(page.getByRole("heading",{name:"Week"})).toBeVisible();
  47  |   await expect(page.locator(".week-card").first()).toBeVisible();
  48  |   await expect(page.getByText("AEROBIC_BASE")).toHaveCount(0);
  49  |   expect(await page.locator("body").evaluate(element=>element.scrollWidth<=window.innerWidth)).toBeTruthy();
  50  |   for (const width of [390,430,768,1280]) {
  51  |     await page.setViewportSize({width,height:900});
  52  |     expect(await page.locator("body").evaluate(element=>element.scrollWidth<=window.innerWidth)).toBeTruthy();
  53  |   }
  54  | });
  55  | 
  56  | test("Week navigation advances within the saved PlanVersion and preserves its URL week", async ({page}) => {
  57  |   await page.goto("/week");
  58  |   await expect(page.getByText("Previous week")).toBeDisabled();
  59  |   const first=await page.locator(".week-nav b").innerText();
> 60  |   await page.getByText("Next week").click();
      |                                     ^ Error: locator.click: Test timeout of 30000ms exceeded.
  61  |   await expect(page.locator(".week-nav b")).not.toHaveText(first);
  62  |   const second=await page.locator(".week-nav b").innerText();
  63  |   await expect(page).toHaveURL(/week=\d{4}-\d{2}-\d{2}/);
  64  |   await page.reload();
  65  |   await expect(page.locator(".week-nav b")).toHaveText(second);
  66  |   await page.getByText("Previous week").click();
  67  |   await expect(page.locator(".week-nav b")).toHaveText(first);
  68  | });
  69  | 
  70  | test("Season recovers a missing local plan ID from the selected athlete", async ({page}) => {
  71  |   await page.goto("/profile");
  72  |   const session=await page.evaluate(() => JSON.parse(localStorage.getItem("rowing-plan-session-v1")||"{}"));
  73  |   await page.evaluate(value => localStorage.setItem("rowing-plan-session-v1",JSON.stringify({...value,planId:"missing-plan"})),session);
  74  |   await page.goto("/season");
  75  |   await expect(page.getByText("Season plan")).toBeVisible();
  76  |   expect(await page.evaluate(() => JSON.parse(localStorage.getItem("rowing-plan-session-v1")||"{}").planId)).not.toBe("missing-plan");
  77  | });
  78  | 
  79  | test("Onboarding recovers the account athlete when this origin has no local session", async ({page}) => {
  80  |   let creates=0;
  81  |   page.on("request", request => { if (request.method()==="POST" && request.url().endsWith("/athletes")) creates++; });
  82  |   await page.goto("/");
  83  |   await page.evaluate(() => localStorage.clear());
  84  |   await page.goto("/onboarding");
  85  |   await expect(page).toHaveURL(/\/profile/);
  86  |   await expect(page.getByText("Synthetic Step 6B Rower")).toBeVisible();
  87  |   expect(creates).toBe(0);
  88  | });
  89  | 
  90  | test("explicit duplicate-profile selection remains active through Profile navigation and reload", async ({page}) => {
  91  |   let creates=0;
  92  |   page.on("request", request => { if (request.method()==="POST" && request.url().endsWith("/athletes")) creates++; });
  93  |   const accountResponse=page.waitForResponse(response => response.url().endsWith("/account/athlete") && response.status()===200);
  94  |   await page.goto("/profile");
  95  |   const account=await (await accountResponse).json();
  96  |   const selectedId=account.athlete_id;
  97  |   await page.evaluate(() => localStorage.clear());
  98  |   const selected={athlete_id:selectedId,updated_at:"2026-09-05T00:00:00Z",display_name:"Chosen full profile",season_name:"Fall",season_start:"2026-09-01",season_end:"2026-11-08",race_count:2,recurring_activity_count:4,performance_test_count:3,plan_id:""};
  99  |   await page.route("**/api/v1/account/athletes", route => route.fulfill({contentType:"application/json",body:JSON.stringify({athletes:[{...selected,athlete_id:"other-1",display_name:"Older profile"},selected,{...selected,athlete_id:"other-3",display_name:"Another profile"},{...selected,athlete_id:"other-4",display_name:"Fourth profile"}]})}));
  100 |   await page.goto("/onboarding");
  101 |   await expect(page.getByText("We found more than one rowing profile")).toBeVisible();
  102 |   await page.getByRole("button",{name:"Use this profile"}).nth(1).click();
  103 |   await expect(page).toHaveURL(/\/profile/);
  104 |   await expect(page.getByText("Synthetic Step 6B Rower")).toBeVisible();
  105 |   expect(await page.evaluate(() => JSON.parse(localStorage.getItem("rowing-plan-session-v1")||"{}").athleteId)).toBe(selectedId);
  106 |   await page.reload();
  107 |   await expect(page.getByText("Synthetic Step 6B Rower")).toBeVisible();
  108 |   expect(creates).toBe(0);
  109 | });
  110 | 
```