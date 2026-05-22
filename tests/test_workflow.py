from src.orchestrator.workflow import StepStatus, WorkflowManager, WorkflowStep


class TestWorkflowConditionEvaluation:
    def test_false_condition_skips_step_without_handler_side_effect(self):
        manager = WorkflowManager()
        workflow = manager.create_workflow("skip guarded step")
        calls = []

        workflow.add_step(
            WorkflowStep(
                "guarded",
                handler=lambda: calls.append("ran"),
                condition=lambda: False,
            )
        )

        assert manager.execute_workflow(workflow.id)
        assert calls == []
        assert workflow.status == StepStatus.COMPLETED
        assert workflow.steps[0].status == StepStatus.SKIPPED
        assert any(
            entry["reason"] == "condition_false"
            for entry in manager.audit_log()
        )

    def test_condition_lifecycle_side_effect_is_rejected_and_restored(self):
        manager = WorkflowManager()
        workflow = manager.create_workflow("side effect guard")
        calls = []

        def mutating_condition(active_workflow, active_step):
            active_workflow.status = StepStatus.COMPLETED
            active_step.status = StepStatus.COMPLETED
            active_step.result = {"secret": "runtime payload"}
            return True

        workflow.add_step(
            WorkflowStep(
                "guarded",
                handler=lambda: calls.append("ran"),
                condition=mutating_condition,
            )
        )

        assert not manager.execute_workflow(workflow.id)
        assert calls == []
        assert workflow.status == StepStatus.FAILED
        assert workflow.steps[0].status == StepStatus.FAILED
        assert workflow.steps[0].result is None
        assert (
            workflow.steps[0].error
            == "condition attempted lifecycle side effects"
        )

        side_effect_entries = [
            entry for entry in manager.audit_log()
            if entry["reason"] == "condition_side_effect"
        ]
        assert side_effect_entries
        assert all("result" not in entry for entry in side_effect_entries)
        assert all("secret" not in str(entry) for entry in side_effect_entries)

    def test_condition_graph_side_effect_is_rejected_and_restored(self):
        manager = WorkflowManager()
        workflow = manager.create_workflow("graph guard")
        original_step = WorkflowStep(
            "guarded",
            handler=lambda: "done",
        )

        def mutating_condition(active_workflow, active_step):
            active_step.name = "mutated"
            active_workflow.add_step(
                WorkflowStep("injected", handler=lambda: "unsafe")
            )
            return True

        original_step.condition = mutating_condition
        workflow.add_step(original_step)

        assert not manager.execute_workflow(workflow.id)
        assert workflow.status == StepStatus.FAILED
        assert workflow.steps == [original_step]
        assert workflow.steps[0].name == "guarded"
        assert workflow.steps[0].status == StepStatus.FAILED
        assert workflow.steps[0].result is None
        assert any(
            entry["reason"] == "condition_side_effect"
            for entry in manager.audit_log()
        )

    def test_condition_must_return_boolean(self):
        manager = WorkflowManager()
        workflow = manager.create_workflow("condition type guard")
        workflow.add_step(
            WorkflowStep(
                "guarded",
                handler=lambda: "done",
                condition=lambda: "yes",
            )
        )

        assert not manager.execute_workflow(workflow.id)
        assert workflow.status == StepStatus.FAILED
        assert workflow.steps[0].status == StepStatus.FAILED
        assert workflow.steps[0].result is None
        assert (
            workflow.steps[0].error
            == "condition returned non-boolean result"
        )
        assert any(
            entry["reason"] == "condition_non_boolean"
            for entry in manager.audit_log()
        )

    def test_true_condition_runs_handler_after_pre_dispatch_check(self):
        manager = WorkflowManager()
        workflow = manager.create_workflow("allowed step")
        workflow.add_step(
            WorkflowStep(
                "guarded",
                handler=lambda: "done",
                condition=(
                    lambda workflow, step: workflow.get_step(step.id) is step
                ),
            )
        )

        assert manager.execute_workflow(workflow.id)
        assert workflow.status == StepStatus.COMPLETED
        assert workflow.steps[0].status == StepStatus.COMPLETED
        assert workflow.steps[0].result == "done"
        assert any(
            entry["reason"] == "condition_true"
            for entry in manager.audit_log()
        )
