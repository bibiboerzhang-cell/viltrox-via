import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LocaleProvider } from "../../app/providers/LocaleProvider";

const { submitDsarRequest } = vi.hoisted(() => ({ submitDsarRequest: vi.fn() }));
vi.mock("./legalApi", async () => {
  const actual = await vi.importActual<typeof import("./legalApi")>("./legalApi");
  return { ...actual, submitDsarRequest, fetchLegalPolicy: vi.fn() };
});

import { DsarRequestForm, localValidationCode } from "./DsarRequestForm";
import { ApiResponseError } from "../../lib/api";

function renderForm() {
  return render(
    <LocaleProvider>
      <DsarRequestForm policy={null} />
    </LocaleProvider>,
  );
}

function fillValidForm() {
  fireEvent.change(screen.getByLabelText("申请类型"), { target: { value: "do_not_contact" } });
  fireEvent.change(screen.getByLabelText("平台"), { target: { value: "youtube" } });
  fireEvent.change(screen.getByLabelText("账号名"), { target: { value: "@demo_creator" } });
  fireEvent.change(screen.getByLabelText("回复邮箱"), { target: { value: "creator@example.com" } });
  fireEvent.click(screen.getByLabelText(/我确认我是该账号本人/));
}

describe("DsarRequestForm", () => {
  beforeEach(() => {
    window.localStorage.clear();
    submitDsarRequest.mockReset();
  });

  it("本地校验与后端同口径,未同意前不发请求", () => {
    renderForm();
    fireEvent.click(screen.getByRole("button", { name: "提交申请" }));
    expect(screen.getByRole("alert")).toHaveTextContent("请选择申请类型");
    expect(submitDsarRequest).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("申请类型"), { target: { value: "erasure" } });
    fireEvent.change(screen.getByLabelText("平台"), { target: { value: "tiktok" } });
    fireEvent.change(screen.getByLabelText("账号名"), { target: { value: "demo_creator" } });
    fireEvent.change(screen.getByLabelText("回复邮箱"), { target: { value: "creator@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "提交申请" }));
    expect(screen.getByRole("alert")).toHaveTextContent("请确认你是该账号本人");
    expect(submitDsarRequest).not.toHaveBeenCalled();
  });

  it("提交成功只展示回执号与 SLA,不回显邮箱;蜜罐字段真人不可见", async () => {
    submitDsarRequest.mockResolvedValue({
      status: "received", public_ref: "DSAR-ABCD1234", request_type: "do_not_contact", sla_days: 30, suppression: { status: "recorded" },
    });
    const { container } = renderForm();

    const honeypot = container.querySelector("#dsar-website") as HTMLInputElement;
    expect(honeypot).toBeTruthy();
    expect(honeypot.tabIndex).toBe(-1);
    expect(honeypot.closest('[aria-hidden="true"]')).not.toBeNull();
    expect(screen.getByTestId("dsar-captcha-token")).toHaveAttribute("type", "hidden");

    fillValidForm();
    fireEvent.click(screen.getByRole("button", { name: "提交申请" }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("DSAR-ABCD1234");
    expect(status).toHaveTextContent("30 天内");
    expect(status).toHaveTextContent("已进入勿联系名单");
    expect(status).not.toHaveTextContent("creator@example.com");
    expect(submitDsarRequest).toHaveBeenCalledTimes(1);
    expect(submitDsarRequest.mock.calls[0][0]).toMatchObject({
      request_type: "do_not_contact", platform: "youtube", handle: "@demo_creator", contact_email: "creator@example.com",
      consent_confirmed: true, website: "", captcha_token: "",
    });
  });

  it("后端稳定 code 映射为可读文案;429 提示限流", async () => {
    submitDsarRequest.mockRejectedValueOnce(
      new ApiResponseError({ status: 400, statusText: "Bad Request" } as unknown as Response, { detail: { code: "contact_email_invalid", message: "x" } }),
    );
    renderForm();
    fillValidForm();
    fireEvent.click(screen.getByRole("button", { name: "提交申请" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("回复邮箱格式无效"));

    submitDsarRequest.mockRejectedValueOnce(
      new ApiResponseError({ status: 429, statusText: "Too Many Requests" } as unknown as Response, { detail: "Too many requests" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "提交申请" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("每小时最多 5 次"));
  });

  it("localValidationCode 与后端 validate_public_request 闭集一致", () => {
    const base = {
      request_type: "erasure" as const, platform: "youtube", handle: "@ok.handle-1", profile_url: "",
      contact_email: "a@b.co", message: "", consent_confirmed: true, captcha_token: "", website: "",
    };
    expect(localValidationCode(base)).toBe("");
    expect(localValidationCode({ ...base, request_type: "" })).toBe("request_type_invalid");
    expect(localValidationCode({ ...base, platform: "" })).toBe("platform_invalid");
    expect(localValidationCode({ ...base, handle: "bad handle!" })).toBe("handle_invalid");
    expect(localValidationCode({ ...base, handle: "", profile_url: "http://x.y" })).toBe("profile_url_invalid");
    expect(localValidationCode({ ...base, handle: "", profile_url: "" })).toBe("subject_missing");
    expect(localValidationCode({ ...base, contact_email: "nope" })).toBe("contact_email_invalid");
    expect(localValidationCode({ ...base, consent_confirmed: false })).toBe("consent_required");
  });
});
