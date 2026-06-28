import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import EvaluationInspector from "./EvaluationInspector";
import type { LoopDetail } from "../types";

function detail(): LoopDetail {
  return {
    lineage_loop_ids: ["loop_0", "loop_1"],
    loop: {
      run_id: "run_1",
      loop_id: "loop_1",
      index: 1,
      parent_loop_id: "loop_0",
      candidate: {
        sequence: "ACDEFGHIKLMNPQRSTVWY",
        origin: "derived",
        structure_artifacts: [],
        boltz_artifacts: [],
        metadata: {}
      },
      conditions: {},
      change_set: null,
      evaluations: [
        {
          kind: "screening",
          evaluator_name: "trs",
          evaluator_version: "test",
          metrics: [{ name: "trs_total", value: 4.2, higher_is_better: true, metadata: {} }],
          passed: true,
          summary: "TRS screening completed.",
          artifacts: [],
          metadata: { trs_components: { coordination_number: 2.1, adjacency_change: 1.4 }, weights: {} },
          created_at: "2026-06-27T12:00:00Z"
        },
        {
          kind: "verifier",
          evaluator_name: "binding-verifier",
          evaluator_version: "test",
          metrics: [{ name: "verifier_score", value: 0.72, higher_is_better: true, metadata: {} }],
          passed: false,
          summary: "Verifier score below target.",
          artifacts: [],
          metadata: {},
          created_at: "2026-06-27T12:01:00Z"
        }
      ],
      reflection: {
        went_well: ["Candidate stayed stable."],
        went_wrong: [],
        next_actions: ["Try a conservative substitution."],
        notes: null
      },
      human_inputs: [],
      status: "active",
      created_at: "2026-06-27T12:00:00Z",
      metadata: {}
    }
  };
}

describe("EvaluationInspector", () => {
  it("renders verifier metrics", () => {
    render(<EvaluationInspector detail={detail()} />);

    expect(screen.getByText("binding-verifier")).toBeInTheDocument();
    expect(screen.getByText("verifier_score")).toBeInTheDocument();
    expect(screen.getByText("0.720")).toBeInTheDocument();
  });

  it("renders TRS component bars", async () => {
    render(<EvaluationInspector detail={detail()} />);

    await userEvent.click(screen.getByRole("tab", { name: "TRS" }));

    expect(screen.getByText("TRS components")).toBeInTheDocument();
    expect(screen.getByText("coordination_number")).toBeInTheDocument();
    expect(screen.getByText("adjacency_change")).toBeInTheDocument();
  });
});
