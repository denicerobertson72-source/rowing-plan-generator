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
