import { render, screen } from "@testing-library/react";

import App from "./App";

test("renders the app shell", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: /Agent Control Plane/i })).toBeInTheDocument();
});
