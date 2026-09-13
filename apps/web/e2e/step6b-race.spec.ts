import { test, expect } from "@playwright/test";

test("Account login uses password-manager fields without app password storage", async ({page}) => {
  await page.goto("/account");
  const email=page.getByLabel("Email"), password=page.getByLabel("Password");
  await expect(email).toHaveAttribute("autocomplete","username");
  await expect(password).toHaveAttribute("autocomplete","current-password");
  await password.fill("never-store-this-password");
  expect(await page.evaluate(() => Object.keys(localStorage).every(key => !localStorage.getItem(key)?.includes("never-store-this-password")))).toBeTruthy();
  expect(await page.evaluate(() => Object.keys(sessionStorage).every(key => !sessionStorage.getItem(key)?.includes("never-store-this-password")))).toBeTruthy();
});

test("Profile accepts an empty preferred long-session day through save and generation", async ({page}) => {
  await page.goto("/profile"); await expect(page.getByText("Synthetic Step 6B Rower")).toBeVisible();
  const days=page.locator('input[name^="preferred-long-"]');
  for (let index=0;index<await days.count();index++) if (await days.nth(index).isChecked()) await days.nth(index).uncheck();
  let savedPreference:unknown;
  page.on("request",request=>{if(request.method()==="PUT"&&request.url().includes("/athletes/")) savedPreference=JSON.parse(request.postData()||"{}").athlete_profile?.preferences?.preferred_long_session_days;});
  await page.getByRole("button",{name:"Save scheduling preferences"}).click();
  await expect(page.getByRole("status")).toContainText("Scheduling preferences saved");
  expect(savedPreference).toEqual([]);
  await page.getByRole("button",{name:"Update plan with these choices"}).click();
  await expect(page.getByRole("status")).toContainText("Plan updated");
  expect(await days.evaluateAll(items=>items.every(item=>!(item as HTMLInputElement).checked))).toBeTruthy();
});

test("Profile maps server capacity validation errors to the field", async ({page}) => {
  await page.setViewportSize({width:390,height:844}); await page.goto("/profile");
  await page.route("**/api/v1/athletes/**",route=>route.fulfill({status:422,contentType:"application/json",body:JSON.stringify({detail:{validation_errors:["Current rowing sessions per week must be a non-negative whole number."]}})}));
  await page.getByRole("button",{name:"Save profile details"}).click();
  const field=page.getByLabel("Current rowing sessions/week");
  await expect(field).toHaveAttribute("aria-invalid","true");
  const described=await field.getAttribute("aria-describedby"); expect(described).toBeTruthy();
  await expect(page.locator(`#${described}`)).toContainText("must be a non-negative whole number");
  expect(await field.evaluate(element=>document.activeElement===element)).toBeTruthy();
  expect(await field.evaluate(element=>{const box=element.getBoundingClientRect();return box.top>=0&&box.bottom<window.innerHeight-56;})).toBeTruthy();
  expect(await page.locator("body").evaluate(element=>element.scrollWidth<=window.innerWidth)).toBeTruthy();
});

test("Profile shows an unmapped server validation error without marking fields", async ({page}) => {
  await page.goto("/profile");
  await page.route("**/api/v1/athletes/**",route=>route.fulfill({status:422,contentType:"application/json",body:JSON.stringify({detail:{validation_errors:["Maximum heart rate must be greater than resting heart rate."]}})}));
  await page.getByRole("button",{name:"Save profile details"}).click();
  await expect(page.getByText("Maximum heart rate must be greater than resting heart rate.")).toBeVisible();
  await expect(page.locator('[aria-invalid="true"]')).toHaveCount(0);
  await expect(page.getByText("highlighted",{exact:false})).toHaveCount(0);
});

test("Profile distinguishes a generation 422 from a profile-save validation failure", async ({page}) => {
  await page.goto("/profile"); await expect(page.getByText("Synthetic Step 6B Rower")).toBeVisible();
  const before=await page.evaluate(()=>JSON.parse(localStorage.getItem("rowing-plan-session-v1")||"{}")); let profileWrites=0;
  page.on("request",request=>{if(request.method()==="PUT"&&request.url().includes("/athletes/"))profileWrites++;});
  await page.route("**/api/v1/athletes/*/plans/generate",route=>route.fulfill({status:422,contentType:"application/json",body:JSON.stringify({detail:{error_code:"planning_conflict",planning_conflicts:["Your fixed training commitments leave no feasible day for strength."]}})}));
  await page.getByRole("button",{name:"Update plan with these choices"}).click();
  const alert=page.getByRole("alert").filter({hasText:"Your profile was saved, but"});
  await expect(alert).toContainText("Your profile was saved, but we couldn't update your plan.");
  await expect(alert).toContainText("leave no feasible day for strength");
  expect(profileWrites).toBe(0);
  expect(await page.evaluate(()=>JSON.parse(localStorage.getItem("rowing-plan-session-v1")||"{}"))).toEqual(before);
});

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
  // Races render chronologically, so verify the edited card independent of
  // its position after its date moves earlier.
  await expect(page.locator(".race-editor", {hasText:"Synthetic Nov A"})).toContainText("2026-09-15");
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
  for (const [width,height] of [[375,667],[390,844],[430,932],[844,390],[768,1024],[1280,900]]) {
    await page.setViewportSize({width,height});
    expect(await page.locator("body").evaluate(element=>element.scrollWidth<=window.innerWidth)).toBeTruthy();
  }
});

test("Week navigation advances within the saved PlanVersion and preserves its URL week", async ({page}) => {
  await page.goto("/week");
  await expect(page.locator(".week-nav b")).not.toHaveText("Plan week");
  await expect(page.getByText("Previous week")).toBeEnabled();
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

test("Week defaults to the browser-local current week while explicit links stay authoritative", async ({page}) => {
  await page.goto("/week?week=2026-08-31");
  await expect(page.locator(".week-nav b")).toContainText("Aug 31");
  await page.getByRole("link",{name:"Week",exact:true}).click();
  await expect(page.locator(".week-nav b")).toContainText("Sep 7");
  await page.goto("/week?week=2026-09-14");
  await expect(page.locator(".week-nav b")).toContainText("Sep 14");
  await page.goto("/week?week=2026-08-31");
  await page.getByRole("button",{name:"This week",exact:true}).click();
  await expect(page.locator(".week-nav b")).toContainText("Sep 7");
});

test("Season recovers a missing local plan ID from the selected athlete", async ({page}) => {
  await page.goto("/profile");
  await expect(page.getByText("Synthetic Step 6B Rower")).toBeVisible();
  const session=await page.evaluate(() => JSON.parse(localStorage.getItem("rowing-plan-session-v1")||"{}"));
  await page.evaluate(value => localStorage.setItem("rowing-plan-session-v1",JSON.stringify({...value,planId:"missing-plan"})),session);
  await page.goto("/season");
  await expect(page.getByRole("heading",{name:"Season arc"})).toBeVisible();
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("rowing-plan-session-v1")||"{}").planId)).not.toBe("missing-plan");
});

test("Season keeps a loaded PlanVersion when selected-profile metadata fails", async ({page}) => {
  let regenerations=0, accountProfileReads=0;
  page.on("request", request => { if (request.method()==="POST" && request.url().includes("/plans/generate")) regenerations++; if (request.url().endsWith("/account/athlete")) accountProfileReads++; });
  await page.goto("/profile");
  await expect(page.getByText("Synthetic Step 6B Rower")).toBeVisible();
  const session=await page.evaluate(() => JSON.parse(localStorage.getItem("rowing-plan-session-v1")||"{}"));
  accountProfileReads=0;
  await page.route(`**/api/v1/athletes/${session.athleteId}`, route => route.fulfill({status:500,body:"profile metadata unavailable"}));
  await page.goto("/season");
  await expect(page.getByRole("heading",{name:"Season arc"})).toBeVisible();
  await expect(page.getByText("Training season")).toBeVisible();
  expect(accountProfileReads).toBe(0);
  expect(regenerations).toBe(0);
});

test("Season reserves error and empty states for required PlanVersion failures", async ({page}) => {
  await page.goto("/profile");
  await expect(page.getByText("Synthetic Step 6B Rower")).toBeVisible();
  const session=await page.evaluate(() => JSON.parse(localStorage.getItem("rowing-plan-session-v1")||"{}"));
  await page.evaluate(value => localStorage.setItem("rowing-plan-session-v1",JSON.stringify({...value,planId:"forced-plan-id"})),session);
  await page.route("**/api/v1/plans/forced-plan-id", route => route.fulfill({status:500,body:"plan unavailable"}));
  await page.goto("/season");
  await expect(page.getByText("Couldn’t load this season. Please try again.")).toBeVisible();
  await page.unroute("**/api/v1/plans/forced-plan-id");
  await page.evaluate(() => { const saved=JSON.parse(localStorage.getItem("rowing-plan-session-v1")||"{}"); localStorage.setItem("rowing-plan-session-v1",JSON.stringify({...saved,planId:""})); });
  await page.route(`**/api/v1/athletes/${session.athleteId}/plans/latest`, route => route.fulfill({status:404,body:"No PlanVersion exists"}));
  await page.goto("/season");
  await expect(page.getByText("No Plan Version Exists Yet")).toBeVisible();
});

test("Season roadmap uses the saved PlanVersion and links to its weeks", async ({page}) => {
  let regenerations=0;
  page.on("request", request => { if (request.method()==="POST" && request.url().includes("/plans/generate")) regenerations++; });
  await page.goto("/profile");
  await expect(page.getByText("Synthetic Step 6B Rower")).toBeVisible();
  await page.goto("/season");
  await expect(page.getByRole("heading",{name:"Season arc"})).toBeVisible();
  await expect(page.getByRole("heading",{name:"Weekly rowing minutes"})).toBeVisible();
  await expect(page.getByText("Final planned rowing minutes only; strength and optional cardio are excluded.")).toBeVisible();
  await expect(page.getByText(/peak \/ key race/)).toBeVisible();
  const links=page.locator("a.season-week");
  await expect(links).toHaveCount(10);
  await links.nth(2).click();
  await expect(page).toHaveURL(/\/week\?week=2026-09-14/);
  await expect(page.locator(".week-nav b")).toContainText("Sep 14");
  expect(regenerations).toBe(0);
  for (const [width,height] of [[375,667],[390,844],[430,932],[844,390],[768,1024],[1280,900]]) { await page.setViewportSize({width,height}); expect(await page.locator("body").evaluate(element=>element.scrollWidth<=window.innerWidth)).toBeTruthy(); }
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
  for (const [width,height] of [[375,667],[390,844],[430,932],[844,390],[768,1024],[1280,900]]) {
    await page.setViewportSize({width,height});
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
  await expect(page.getByLabel("Typical strength session duration")).toHaveValue("60");
  await page.getByLabel("Typical strength session duration").fill("45");
  await page.getByLabel("Plan setting").selectOption("planned");
  await page.getByRole("button",{name:"Save activity"}).click();
  await expect(page.getByRole("status")).toContainText("Training schedule saved");
  await strength.getByRole("button",{name:"Edit scheduling"}).click();
  await expect(page.getByRole("group",{name:"Preferred days"}).getByRole("checkbox",{name:"Monday"})).toBeChecked();
  await expect(page.getByLabel("Typical strength session duration")).toHaveValue("45");
  await expect(page.getByLabel("Plan setting")).toHaveValue("planned");
});

test("Race editor distinguishes event dates, competition dates, and course practice", async ({page}) => {
  await page.goto("/profile");
  await page.getByRole("button",{name:"Add race"}).click();
  await page.getByLabel("Race name").fill("Two-day course event");
  await page.getByLabel("Event start date").fill("2026-10-16");
  await page.getByLabel("Event end date").fill("2026-10-17");
  const competition=page.getByRole("group",{name:"Actual competition day(s)"}).locator('input[type="checkbox"]');
  const practice=page.getByRole("group",{name:"Optional event sessions"}).locator('input[type="checkbox"]');
  await competition.nth(0).uncheck();
  await competition.nth(1).check();
  await practice.nth(0).check();
  await page.getByRole("button",{name:"Save race"}).click();
  await expect(page.getByRole("status")).toContainText("Race saved");
});
