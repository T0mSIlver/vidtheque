// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Frame, FrameShot, type Shot } from "./Frame";

function shot(over: Partial<Shot> = {}): Shot {
  return {
    thumb: "https://api.test/frames/v-00007.jpg?w=320&q=70",
    thumb_large: "https://api.test/frames/v-00007.jpg?w=960&q=70",
    title: "Context engineering",
    channel: "AI Engineer",
    video_id: "BiG2ssibKGc",
    timestamp: "2:18",
    link: "https://youtu.be/BiG2ssibKGc?t=136",
    ...over,
  };
}

describe("Frame", () => {
  it("names the channel when the moment has no keyframe", () => {
    render(<Frame src={null} alt="" label="on-screen" />);
    expect(screen.getByText("on-screen")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  // A dead frame URL used to leave a blank box, which reads as a corpus with a
  // hole in it rather than as one image that did not arrive.
  it("falls back to the placeholder when the bytes never arrive", () => {
    render(<Frame src="https://api.test/frames/gone.jpg" alt="a slide" label="frame" />);
    fireEvent.error(screen.getByRole("img"));
    expect(screen.getByText("frame")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("tries again when the card is handed a different frame", () => {
    const { rerender } = render(<Frame src="https://api.test/a.jpg" alt="a" />);
    fireEvent.error(screen.getByRole("img"));
    expect(screen.queryByRole("img")).not.toBeInTheDocument();

    rerender(<Frame src="https://api.test/b.jpg" alt="b" />);
    expect(screen.getByRole("img")).toBeInTheDocument();
  });
});

describe("FrameShot", () => {
  // The server markup is the hydrated markup: no plain image swapped for a
  // button after capability detection.
  it("renders the enlarge control in the server markup", () => {
    const html = renderToString(<FrameShot shot={shot()} alt="" />);
    expect(html).toContain('aria-label="Enlarge the frame from Context engineering at 2:18"');
    expect(html).toContain("<dialog");
  });

  it("enlarges the frame the server sized, and never one it did not", async () => {
    const user = userEvent.setup();
    render(<FrameShot shot={shot()} alt="" />);

    const button = screen.getByRole("button", {
      name: "Enlarge the frame from Context engineering at 2:18",
    });
    // The large image is not fetched until a visitor asks for it.
    expect(screen.queryByAltText(/^Frame from/)).not.toBeInTheDocument();

    await user.click(button);
    const large = screen.getByAltText("Frame from Context engineering at 2:18");
    expect(large).toHaveAttribute("src", "https://api.test/frames/v-00007.jpg?w=960&q=70");

    // The caption is the talk, the channel and the second; the receipt is where
    // it came from, printed rather than implied.
    expect(screen.getByText("Context engineering · AI Engineer · 2:18")).toBeInTheDocument();
    const slab = document.querySelector('a[href="https://youtu.be/BiG2ssibKGc?t=136"]');
    expect(slab).toHaveTextContent("youtu.be/BiG2ssibKGc?t=136");
    expect(slab).toHaveAttribute("target", "_blank");
  });

  it("releases the large frame on close and returns focus to its thumbnail", async () => {
    const user = userEvent.setup();
    render(<FrameShot shot={shot()} alt="" />);
    const button = screen.getByRole("button", { name: /^Enlarge the frame/ });

    await user.click(button);
    await user.click(screen.getByRole("button", { name: "close" }));

    expect(screen.queryByAltText(/^Frame from/)).not.toBeInTheDocument();
    expect(button).toHaveFocus();
  });

  it("dismisses on a click that landed on the backdrop and not the picture", async () => {
    const user = userEvent.setup();
    const { container } = render(<FrameShot shot={shot()} alt="" />);
    await user.click(screen.getByRole("button", { name: /^Enlarge the frame/ }));

    const dialog = container.querySelector("dialog") as HTMLDialogElement;
    fireEvent.click(dialog);
    expect(dialog.open).toBe(false);

    // A click inside the picture is not a dismissal.
    await user.click(screen.getByRole("button", { name: /^Enlarge the frame/ }));
    fireEvent.click(screen.getByText("Context engineering · AI Engineer · 2:18"));
    expect(dialog.open).toBe(true);
  });

  // The lift and the ring are `.shotButton`'s, so what a test can hold is the
  // class the button carries and the image inside it: a rule keyed on `a`
  // alone is what left every demo still dimmed for good.
  it("carries the class the lift and the ring are keyed on", () => {
    render(<FrameShot shot={shot()} alt="" />);
    const button = screen.getByRole("button", { name: /^Enlarge the frame/ });
    expect(button.className).toMatch(/shotButton/);
    expect(button.querySelector("img")?.className).toMatch(/img/);
  });

  it("offers no enlarge control for a moment with no frame behind it", () => {
    render(<FrameShot shot={shot({ thumb: null, thumb_large: null })} alt="" label="spoken" />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText("spoken")).toBeInTheDocument();
  });

  it("falls back to the video itself when the hit carries no deep link", async () => {
    const user = userEvent.setup();
    render(<FrameShot shot={shot({ link: null })} alt="" />);
    await user.click(screen.getByRole("button", { name: /^Enlarge the frame/ }));
    expect(document.querySelector('a[href="https://youtu.be/BiG2ssibKGc"]')).toHaveTextContent(
      "youtu.be/BiG2ssibKGc",
    );
  });
});
