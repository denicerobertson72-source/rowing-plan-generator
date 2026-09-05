import { test, expect } from "@playwright/test";

test("Step 6B race draft create, failure guard, duplicate guard, and plan invalidation", async ({page}) => {
  let writes=0;
  page.on("request", request => { if (request.method()==="PUT" && request.url().includes("/athletes/")) writes++; });
  await page.goto("/profile");
  await expect(page.getByText("Synthetic Step 6B Rower")).toBeVisible();
  const initial=page.locator(".race-editor");
  await expect(initial).toHaveCount(3);
  await page.getByRole("button",{name:"Add race"}).click();
  await page.getByLabel("Race name").fill("Synthetic Oct 5");
  await page.getByLabel("Start date").fill("2026-10-05");
  await page.getByLabel("Priority").selectOption("B");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(initial).toHaveCount(3);
  await page.getByRole("button",{name:"Save race"}).dblclick();
  await expect(page.getByRole("status")).toContainText("Race saved");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator(".race-editor")).toHaveCount(4);
  expect(writes).toBe(1);
  const text=await page.locator(".race-editor").allTextContents();
  expect(text.join(" ")).toMatch(/Sep C[\s\S]*Oct 5[\s\S]*Oct B[\s\S]*Nov A/);
  await page.getByRole("button",{name:"Edit race"}).last().click();
  await page.getByLabel("Start date").fill("2026-09-15");
  await expect(page.locator(".race-editor").last()).toContainText("Synthetic Nov A");
  await page.getByRole("button",{name:"Save race"}).click();
  await expect(page.getByRole("status")).toContainText("Race saved");
  expect((await page.locator(".race-editor").first().innerText())).toContain("Synthetic Nov A");
  await page.getByRole("button",{name:"Edit race"}).first().click();
  await page.getByLabel("Race name").fill("Failure stays local");
  await page.route("**/api/v1/athletes/**", route => route.fulfill({status:500,body:"failure"}));
  await page.getByRole("button",{name:"Save race"}).click();
  await expect(page.getByRole("alert").filter({hasText:"Couldn’t save"})).toContainText("Couldn’t save");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByLabel("Race name")).toHaveValue("Failure stays local");
  await page.unroute("**/api/v1/athletes/**");
});

test("Week presents semantic cards without internal role labels or narrow-screen overflow", async ({page}) => {
  await page.setViewportSize({width:375,height:812});
  await page.goto("/profile");
  await expect(page.getByText("Synthetic Step 6B Rower")).toBeVisible();
  await page.getByRole("button",{name:"Update plan with these choices"}).click();
  await expect(page.getByRole("status")).toContainText("Plan updated");
  await page.goto("/week");
  await expect(page.getByRole("heading",{name:"Week"})).toBeVisible();
  await expect(page.locator(".week-card").first()).toBeVisible();
  await expect(page.getByText("AEROBIC_BASE")).toHaveCount(0);
  expect(await page.locator("body").evaluate(element=>element.scrollWidth<=window.innerWidth)).toBeTruthy();
  for (const width of [390,430,768,1280]) {
    await page.setViewportSize({width,height:900});
    expect(await page.locator("body").evaluate(element=>element.scrollWidth<=window.innerWidth)).toBeTruthy();
  }
});

test("Week navigation advances within the saved PlanVersion and preserves its URL week", async ({page}) => {
  await page.goto("/week");
  await expect(page.locator(".week-nav b")).not.toHaveText("Plan week");
  await expect(page.getByText("Previous week")).toBeDisabled();
  const first=await page.locator(".week-nav b").innerText();
  await page.getByText("Next week").click();
  await expect(page.locator(".week-nav b")).not.toHaveText(first);
  const second=await page.locator(".week-nav b").innerText();
  await expect(page).toHaveURL(/week=\d{4}-\d{2}-\d{2}/);
  await page.reload();
  await expect(page.locator(".week-nav b")).toHaveText(second);
  await page.getByText("Previous week").click();
  await expect(page.locator(".week-nav b")).toHaveText(first);
});

test("Season recovers a missing local plan ID from the selected athlete", async ({page}) => {
  await page.goto("/profile");
  const session=await page.evaluate(() => JSON.parse(localStorage.getItem("rowing-plan-session-v1")||"{}"));
  await page.evaluate(value => localStorage.setItem("rowing-plan-session-v1",JSON.stringify({...value,planId:"missing-plan"})),session);
  await page.goto("/season");
  await expect(page.getByText("Season plan")).toBeVisible();
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("rowing-plan-session-v1")||"{}").planId)).not.toBe("missing-plan");
});

test("Onboarding recovers the account athlete when this origin has no local session", async ({page}) => {
  let creates=0;
  page.on("request", request => { if (request.method()==="POST" && request.url().endsWith("/athletes")) creates++; });
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.goto("/onboarding");
  await expect(page).toHaveURL(/\/profile/);
  await expect(page.getByText("Synthetic Step 6B Rower")).toBeVisible();
  expect(creates).toBe(0);
});

test("explicit duplicate-profile selection remains active through Profile navigation and reload", async ({page}) => {
  let creates=0;
  page.on("request", request => { if (request.method()==="POST" && request.url().endsWith("/athletes")) creates++; });
  const accountResponse=page.waitForResponse(response => response.url().endsWith("/account/athlete") && response.status()===200);
  await page.goto("/profile");
  const account=await (await accountResponse).json();
  const selectedId=account.athlete_id;
  await page.evaluate(() => localStorage.clear());
  const selected={athlete_id:selectedId,updated_at:"2026-09-05T00:00:00Z",display_name:"Chosen full profile",season_name:"Fall",season_start:"2026-09-01",season_end:"2026-11-08",race_count:2,recurring_activity_count:4,performance_test_count:3,plan_id:""};
  await page.route("**/api/v1/account/athletes", route => route.fulfill({contentType:"application/json",body:JSON.stringify({athletes:[{...selected,athlete_id:"other-1",display_name:"Older profile"},selected,{...selected,athlete_id:"other-3",display_name:"Another profile"},{...selected,athlete_id:"other-4",display_name:"Fourth profile"}]})}));
  await page.goto("/onboarding");
  await expect(page.getByText("We found more than one rowing profile")).toBeVisible();
  await page.getByRole("button",{name:"Use this profile"}).nth(1).click();
  await expect(page).toHaveURL(/\/profile/);
  await expect(page.getByText("Synthetic Step 6B Rower")).toBeVisible();
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("rowing-plan-session-v1")||"{}").athleteId)).toBe(selectedId);
  await page.reload();
  await expect(page.getByText("Synthetic Step 6B Rower")).toBeVisible();
  expect(creates).toBe(0);
});

test("Profile scheduling editor keeps weekday labels clickable and preserves alternate-aerobic mode", async ({page}) => {
  await page.setViewportSize({width:375,height:812});
  await page.goto("/profile");
  const strength=page.locator(".activity-card").filter({hasText:"STRENGTH"});
  await strength.getByRole("button",{name:"Edit scheduling"}).click();
  const preferred=page.getByRole("group",{name:"Preferred days"});
  const monday=preferred.getByRole("checkbox",{name:"Monday"});
  await expect(monday).toBeChecked();
  for (const width of [375,390,430,768,1280]) {
    await page.setViewportSize({width,height:900});
    expect(await page.locator(".day-choice").evaluateAll(rows => rows.every(row => {
      const input=row.querySelector("input")?.getBoundingClientRect(), text=row.querySelector("span")?.getBoundingClientRect();
      return Boolean(input && text && input.left < text.left && Math.abs(input.top-text.top) < 14);
    }))).toBeTruthy();
  }
  await preferred.getByText("Monday",{exact:true}).click();
  await expect(monday).not.toBeChecked();
  await preferred.getByText("Monday",{exact:true}).click();
  await expect(monday).toBeChecked();
  await expect(page.getByText("Alternate aerobic after strength")).toBeVisible();
  await page.getByLabel("Plan setting").selectOption("planned");
  await page.getByRole("button",{name:"Save activity"}).click();
  await expect(page.getByRole("status")).toContainText("Training schedule saved");
  await strength.getByRole("button",{name:"Edit scheduling"}).click();
  await expect(page.getByRole("group",{name:"Preferred days"}).getByRole("checkbox",{name:"Monday"})).toBeChecked();
  await expect(page.getByLabel("Plan setting")).toHaveValue("planned");
});
