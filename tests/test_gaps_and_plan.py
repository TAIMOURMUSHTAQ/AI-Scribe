"""Therapist feedback (scribe_v4.1): missing detail is flagged, the AI proposes plan items, the clinician decides."""
import json

from fastapi.testclient import TestClient

import app as appmod
from scribe.completeness import checklist_gaps
from scribe.facts import check_draft, extract_facts
from scribe.pipeline import normalize_draft
from scribe.prompts import build_draft_message
from scribe.providers import MockProvider

appmod.app.state.provider = MockProvider(delay=0, chunk=400)
client = TestClient(appmod.app)


def gaps_for(text, ctx=None, dismissed=None):
    return checklist_gaps(text, ctx or {}, extract_facts(text), dismissed)


# ---- "how much" for every reported difficulty ------------------------------------------

def test_reported_difficulty_without_severity_is_flagged():
    g = gaps_for("Mother reports the patient finds it difficult to speak.")
    q = [x for x in g if x["key"].startswith("quantify:")]
    assert q and q[0]["priority"] == "high"
    assert "severity" in q[0]["item"] and "frequency" in q[0]["item"]
    assert "how often" in q[0]["ask"]


def test_roman_urdu_difficulty_is_flagged():
    assert any(x["key"].startswith("quantify:") for x in gaps_for("Ammi ne bataya k bachay ko bolne mein mushkil hoti hai."))


def test_quantified_difficulty_is_not_flagged():
    for text in ("Patient finds it very difficult to speak, daily, since 3 months.",
                 "Stuttering noted, 5 blocks per minute in conversation."):
        assert not any(x["key"].startswith("quantify:") for x in gaps_for(text)), text


def test_complete_note_has_no_checklist_gaps():
    text = ("45 min session. /s/ initial words 14/20 with min verbal cues. Mom reports practice 5 days. "
            "Plan: continue phrase level. HEP: cards daily.")
    assert gaps_for(text) == []


def test_any_cue_wording_counts_as_cue_level():
    assert not any(x["key"] == "cue_level" for x in gaps_for("2-step instructions 7/10 with gestural cues."))
    assert any(x["key"] == "cue_level" for x in gaps_for("/s/ words 7/10."))


def test_dismissed_checklist_gap_stays_dismissed():
    assert any(x["key"] == "duration" for x in gaps_for("/s/ 7/10 min cues."))
    assert not any(x["key"] == "duration" for x in gaps_for("/s/ 7/10 min cues.", dismissed=["duration"]))


def test_prompt_carries_checklist_and_dismissed():
    msg = build_draft_message("notes", {}, "- facts", "- C1: Session length not stated", ["Hearing status"])
    assert "<checklist_gaps>" in msg and "C1: Session length" in msg
    assert "<dismissed_gaps>" in msg and "Hearing status" in msg


# ---- plan: AI proposes, clinician decides ------------------------------------------------

def test_ai_proposals_start_pending_and_legacy_missing_info_converts():
    d = normalize_draft({"analysis": {"missing_information": ["Cue level not stated"]},
                         "soap": {"plan": [{"text": "Continue /s/.", "basis": ["F1"], "origin": "clinician"},
                                           {"text": "Probe /s/ at sentence level.", "basis": ["F1"],
                                            "origin": "ai_proposed", "rationale": "70% at phrase level."}]}})
    plan = d["soap"]["plan"]
    assert plan[0]["origin"] == "clinician" and "decision" not in plan[0]
    assert plan[1]["origin"] == "ai_proposed" and plan[1]["decision"] == "pending"
    assert d["analysis"]["gaps"][0]["item"] == "Cue level not stated"


def test_placeholder_dropped_when_there_are_proposals():
    d = normalize_draft({"soap": {"plan": [{"text": "Plan not documented in session input.", "origin": "clinician"},
                                           {"text": "Probe /r/.", "origin": "ai_proposed"}]}})
    assert [p["text"] for p in d["soap"]["plan"]] == ["Probe /r/."]


SRC = "/s/ words 14/20 with min cues. Mother says he choked on rice. Plan: continue phrase level."
FINDINGS = {"findings": [{"id": "F1", "source": "measured", "evidence": "14/20 with min cues"},
                         {"id": "F2", "source": "reported", "evidence": "choked on rice"},
                         {"id": "F3", "source": "clinician_plan", "evidence": "continue phrase level"}],
            "gaps": [{"id": "G1", "item": "Stimulability not documented"}]}


def _draft(plan):
    soap = {s: [{"text": "Not documented in session input.", "basis": []}] for s in ("subjective", "objective", "assessment")}
    soap["objective"] = [{"text": "/s/ words 14/20 (70%) with minimal cues.", "basis": ["F1"]}]
    soap["subjective"] = [{"text": "Mother reported he choked on rice.", "basis": ["F2"]}]
    soap["plan"] = plan
    return {"analysis": FINDINGS, "soap": soap}


def types(plan):
    return [i["type"] for i in check_draft(_draft(plan), SRC, "")["issues"]]


def test_pending_proposal_needs_decision():
    t = types([{"text": "Discuss swallow screening referral with the team.", "basis": ["F2"], "origin": "ai_proposed",
                "decision": "pending"}])
    assert "plan_pending" in t
    assert "unsupported_term" not in t      # labelled proposal, not an AI-inserted decision
    assert "plan_not_stated" not in t


def test_accepted_proposal_is_clean_and_gap_ids_are_valid_basis():
    t = types([{"text": "Continue phrase level.", "basis": ["F3"]},
               {"text": "Assess stimulability for /s/.", "basis": ["G1"], "origin": "ai_proposed", "decision": "accepted"}])
    assert not {"plan_pending", "plan_not_stated", "unknown_basis"} & set(t)


def test_clinician_item_without_plan_source_still_flagged():
    assert "plan_not_stated" in types([{"text": "Add a sensory break schedule.", "basis": ["F1"]}])


def test_pending_proposal_does_not_count_as_covering_a_red_flag():
    src = "/s/ 14/20 min cues. He coughed during meals daily."
    d = {"analysis": {"findings": []}, "soap": {
        "objective": [{"text": "/s/ 14/20 (70%).", "basis": []}],
        "plan": [{"text": "Screen swallowing; he coughed during meals.", "origin": "ai_proposed", "decision": "pending"}]}}
    assert any(i["type"] == "red_flag_missing" for i in check_draft(d, src, "")["issues"])
    d["soap"]["plan"][0]["decision"] = "accepted"
    assert not any(i["type"] == "red_flag_missing" for i in check_draft(d, src, "")["issues"])


# ---- API ----------------------------------------------------------------------------------

NOTE = "Mother reports he finds it difficult to speak in class. /r/ words 6/20. Will continue /r/."


def final_event(body):
    text = client.post("/api/scribe/draft", json=body).text
    block = [b for b in text.split("\n\n") if b.startswith("event: final")][0]
    return json.loads(block.split("\n", 1)[1].removeprefix("data: "))


def test_draft_returns_checklist_gaps_and_pending_proposals():
    final = final_event({"input": NOTE})
    keys = [g["key"] for g in final["facts"]["checklist_gaps"]]
    assert any(k.startswith("quantify:") for k in keys)
    plan = final["draft"]["soap"]["plan"]
    assert any(p["origin"] == "ai_proposed" and p["decision"] == "pending" for p in plan)
    assert final["checks"]["status"] in ("review", "attention")


def test_dismissed_gaps_are_respected_by_draft_and_check():
    final = final_event({"input": NOTE, "dismissed_gaps": ["duration", "home_programme"]})
    keys = {g["key"] for g in final["facts"]["checklist_gaps"]}
    assert not {"duration", "home_programme"} & keys
    chk = client.post("/api/scribe/check", json={"input": NOTE, "analysis": final["draft"]["analysis"],
                                                 "soap": final["draft"]["soap"], "dismissed_gaps": ["duration"]})
    assert chk.status_code == 200


def test_plan_rewrite_keeps_origin_and_pdf_skips_pending():
    final = final_event({"input": NOTE})
    body = {"input": NOTE, "analysis": final["draft"]["analysis"], "soap": final["draft"]["soap"]}
    regen = client.post("/api/scribe/regenerate", json={**body, "section": "plan"}).json()
    assert all("origin" in s for s in regen["statements"])
    soap = final["draft"]["soap"]
    soap["plan"].append({"text": "Accepted proposal.", "basis": [], "origin": "ai_proposed", "decision": "accepted"})
    pdf = client.post("/api/scribe/export", json={"soap": soap, "signed_by": "Dr. Test"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_clarification_answer_resolves_the_quantify_gap():
    text = "Mother reports he finds it difficult to speak in class. /r/ 6/20 min cues."
    gap = [g for g in gaps_for(text) if g["key"].startswith("quantify:")][0]
    answered = text + f"\nClarification ({gap['label']}): moderate, most school days, since about 6 months"
    assert not any(g["key"].startswith("quantify:") for g in gaps_for(answered))


def test_clarification_label_words_do_not_satisfy_checks():
    # "next session" appears only in the label, so the plan gap must stay open.
    text = "/r/ 6/20 min cues.\nClarification (No plan for the next session stated): not decided yet"
    assert any(g["key"] == "plan" for g in gaps_for(text))


def test_ai_gaps_repeating_the_checklist_are_dropped():
    from scribe.completeness import drop_checklist_duplicates
    checklist = gaps_for("Mother reports he finds it difficult to speak in class. Worked on /r/, did ok.",
                         {"goals": "/r/ 80%"})
    ai = [{"kind": "documentation", "item": "Reported difficulty speaking in class is not quantified for severity", "ask": ""},
          {"kind": "documentation", "item": "Severity of the reported feeding problem is not quantified", "ask": ""},
          {"kind": "question", "item": "Home practice was not mentioned", "ask": ""},
          {"kind": "documentation", "item": "Session length was not stated", "ask": "How long was the session?"},
          {"kind": "assessment", "item": "Consistency of /r/ errors not documented", "ask": ""},
          {"kind": "assessment", "item": "Generalisation not documented", "ask": "Any carry-over at home practice?"}]
    for n, g in enumerate(ai, 1):
        g["id"] = f"G{n}"
    kept_gaps, dropped = drop_checklist_duplicates(ai, checklist)
    kept = [g["item"] for g in kept_gaps]
    assert kept == ["Severity of the reported feeding problem is not quantified",
                    "Consistency of /r/ errors not documented", "Generalisation not documented"]
    assert all(v.startswith("C") for v in dropped.values()) and len(dropped) == 3



# ---- stammering case from therapist testing (11 Nov 2026) ----------------------------------

STAMMER = ("Four-year-old boy reported with pauses and blocks. He has no secondary behavior and no family history "
           "of stammering. He was assessed on SSI. he shows repetitions on words such as s and m, his SSI score was 8, "
           "child hesitates while")


def test_stammer_note_checklist_targets_the_complaint_not_the_negations():
    g = gaps_for(STAMMER, {"discipline": "Speech-language therapy"})
    keys = [x["key"] for x in g]
    quantify = [x for x in g if x["key"].startswith("quantify:")]
    assert len(quantify) == 1 and "pauses and blocks" in quantify[0]["item"]
    assert not any("family history" in x["item"] for x in quantify)
    assert {"onset", "fluency_frequency", "unfinished"} <= set(keys)
    assert "cue_level" not in keys            # not relevant to a stuttering assessment


def test_fluency_note_with_onset_and_ss_has_no_fluency_gaps():
    text = "Parent reports stammering daily since 8 months, rated 4/10. 6% syllables stuttered in play. Plan: Lidcombe."
    keys = [x["key"] for x in gaps_for(text)]
    assert "onset" not in keys and "fluency_frequency" not in keys and "cue_level" not in keys


def test_head_hitting_is_a_high_red_flag():
    f = extract_facts("Mother says when he gets stuck he hits his head with his hand.")
    assert any(x["category"] == "self_harm" and x["severity"] == "high" for x in f["red_flags"])


def _soap_check(text, src):
    d = {"analysis": {"findings": [{"id": "F1", "source": "observed", "evidence": src[:20]}]},
         "soap": {"assessment": [{"text": text, "basis": ["F1"]}]}}
    return {i["type"] for i in check_draft(d, src, "")["issues"]}


def test_condition_synonyms_and_labels():
    assert "unsupported_term" not in _soap_check("Stuttering-like disfluencies were observed.", "ghar mein haklahat nahi, blocks")
    assert "unsupported_term" not in _soap_check("Stuttering was observed.", "no family history of stammering")
    assert "unsupported_term" in _soap_check("Consistent with a fluency disorder.", "pauses and blocks")


def test_number_words():
    assert "unsupported_number" not in _soap_check("The 4-year-old boy showed blocks.", "Four-year-old boy with blocks")
    assert "unsupported_number" in _soap_check("Accuracy improved to seventy percent.", "accuracy improved")


def test_ai_idea_labelled_as_clinician_becomes_a_pending_proposal():
    d = normalize_draft({"analysis": {"findings": [{"id": "F1", "source": "observed"}, {"id": "F2", "source": "clinician_plan"}]},
                         "soap": {"plan": [{"text": "The clinician assessed the child on the SSI.", "basis": ["F1"], "origin": "clinician"},
                                           {"text": "Start Lidcombe.", "basis": ["F2"], "origin": "clinician"}]}})
    plan = d["soap"]["plan"]
    assert plan[0]["origin"] == "ai_proposed" and plan[0]["decision"] == "pending"
    assert plan[1]["origin"] == "clinician"


def test_at_most_one_documentation_proposal():
    def prop(text, action):
        return {"text": text, "origin": "ai_proposed", "action": action, "basis": []}
    d = normalize_draft({"soap": {"plan": [prop("Record SSI version.", "document"), prop("Ask about awareness.", "document"),
                                           prop("Take a %SS sample.", "assess"), prop("Discuss monitoring vs Lidcombe.", "treat"),
                                           prop("Coach parent.", "caregiver")]}})
    assert [p["action"] for p in d["soap"]["plan"]] == ["document", "assess", "treat", "caregiver"]
    # but never strip proposals down to fewer than two real actions
    d = normalize_draft({"soap": {"plan": [prop("A.", "document"), prop("B.", "document"), prop("C.", "assess")]}})
    assert len(d["soap"]["plan"]) == 3


def test_developmental_label_is_flagged():
    assert "unsupported_term" in _soap_check("Findings suggest developmental stammering.", "pauses and blocks, no family history of stammering")


def test_assessment_gap_repeating_onset_or_ss_is_dropped_but_others_kept():
    from scribe.completeness import drop_checklist_duplicates
    checklist = gaps_for(STAMMER)
    ai = [{"id": "G1", "kind": "assessment", "item": "Stammering frequency and sample context not documented.", "ask": ""},
          {"id": "G2", "kind": "assessment", "item": "Child awareness and emotional reaction not documented.", "ask": ""}]
    kept, dropped = drop_checklist_duplicates(ai, checklist)
    assert [g["id"] for g in kept] == ["G2"] and "G1" in dropped


# ---- fixes after running cases.json (11 Nov 2026) -------------------------------------------

def test_unlabelled_clinician_plan_is_not_relabelled_as_ai():
    """A1: the model filed the therapist's plan as 'context'; it must stay the therapist's."""
    d = normalize_draft({"analysis": {"findings": [
        {"id": "F7", "source": "context", "evidence": "Weekly 45-min sessions, breathing techniques, phone practice homework"},
        {"id": "F1", "source": "observed", "evidence": "He was assessed on SSI"}]},
        "soap": {"plan": [
            {"text": "Weekly 45-minute sessions were planned, using breathing techniques and phone practice homework.",
             "basis": ["F7"], "origin": "clinician"},
            {"text": "The clinician assessed the child on the SSI.", "basis": ["F1"], "origin": "clinician"}]}})
    plan = d["soap"]["plan"]
    assert plan[0]["origin"] == "clinician"          # the therapist's own words stay theirs
    assert plan[1]["origin"] == "ai_proposed"        # done-today restated as a plan is still caught


def test_hyphenated_session_length_and_unlabelled_plan_and_carryover():
    keys = {g["key"] for g in gaps_for("Four-year-old boy stutters. Plan: weekly 30-min sessions, parent carryover at home.")}
    assert not {"duration", "home_programme", "plan"} & keys          # K4
    keys = {g["key"] for g in gaps_for("Weekly 45-min sessions, breathing techniques.")}
    assert not {"duration", "plan"} & keys                            # K6
    assert extract_facts("1x/wk 45-min session")["durations_min"] == [45]


def test_negated_blocks_do_not_trigger_stammering_questions():
    keys = {g["key"] for g in gaps_for("Client's speech is fluent with no blocks observed today.")}   # B5
    assert not {"onset", "fluency_frequency"} & keys
    assert "onset" in {g["key"] for g in gaps_for("Client blocks on most words.")}


# ---- Objective assessment guide (scribe_v4.3, therapist feedback 11 Nov 2026) ----------------

def test_objective_suggestions_never_carry_values():
    from scribe.pipeline import blank_values
    assert blank_values("%SS 6% in conversation (300 syllables)") == "%SS ___% in conversation (300 syllables)"
    assert blank_values("Longest block 2 seconds") == "Longest block ___ seconds"
    assert blank_values("/r/ initial 14/20, medial ___/20") == "/r/ initial ___/___, medial ___/20"
    assert blank_values("") == "___"


def test_suggestions_normalised_and_empty_objective_reworded():
    d = normalize_draft({"soap": {"objective": [{"text": "Not documented in session input.", "basis": []}]},
                         "objective_suggestions": [
                             {"id": "x", "measure": "%SS in conversation", "method": "300 syllables",
                              "why": "Baseline", "record_as": "%SS 4%", "basis": ["F1"], "priority": "urgent"},
                             {"measure": " "}]})
    assert d["soap"]["objective"][0]["text"] == "No objective measures were recorded in this session."
    m = d["objective_suggestions"]
    assert len(m) == 1 and m[0]["id"] == "M1" and m[0]["priority"] == "medium" and m[0]["record_as"] == "%SS ___%"


def test_assessment_gap_repeating_a_measure_is_dropped():
    from scribe.completeness import drop_measure_duplicates
    measures = [{"id": "M1", "measure": "Overall intelligibility in connected speech", "method": "Conversation sample"}]
    gaps = [{"id": "G1", "kind": "assessment", "item": "Intelligibility in connected speech was not documented."},
            {"id": "G2", "kind": "question", "item": "Intelligibility at school is not described."},
            {"id": "G3", "kind": "assessment", "item": "Oral-motor structure was not documented."}]
    kept, dropped = drop_measure_duplicates(gaps, measures)
    assert [g["id"] for g in kept] == ["G2", "G3"] and dropped == {"G1": "M1"}


def test_result_lines_count_as_the_clinicians_words():
    text = "Mother reports blocks daily since 6 months.\nResult (Stuttering frequency (%SS)): 6% in conversation, 300 syllables"
    keys = {g["key"] for g in gaps_for(text)}
    assert "fluency_frequency" not in keys


def test_api_returns_objective_suggestions_and_check_accepts_them():
    final = final_event({"input": "Mother reports he finds it difficult to speak in class."})
    m = final["draft"]["objective_suggestions"]
    assert m and m[0]["id"] == "M1"
    soap = final["draft"]["soap"]
    soap["plan"].append({"text": "Next session: assess accuracy.", "basis": ["M1"], "origin": "clinician", "edited": True})
    chk = client.post("/api/scribe/check", json={"input": "Mother reports he finds it difficult to speak in class.",
                                                 "analysis": final["draft"]["analysis"], "soap": soap,
                                                 "objective_suggestions": m}).json()
    assert not any(i["type"] == "unknown_basis" for i in chk["issues"])


def test_questions_and_referrals_are_not_objective_measures():
    d = normalize_draft({"objective_suggestions": [
        {"measure": "Caregiver report of impact", "method": "Ask the caregiver how it affects routines."},
        {"measure": "Hearing status", "method": "Complete a brief hearing screen or refer for one."},
        {"measure": "Receptive language probe", "method": "Give one- and two-step directions; record accuracy."}]})
    assert [m["measure"] for m in d["objective_suggestions"]] == ["Receptive language probe"]


def test_age_from_context_is_an_allowed_number():
    from scribe.schema import PatientContext
    ctx = PatientContext(patient_age="3 y")
    d = {"analysis": {"findings": []}, "soap": {"plan": [{"text": "Discuss options for a 3-year-old.", "origin": "ai_proposed",
                                                          "decision": "accepted"}]}}
    assert not any(i["type"] == "unsupported_number" for i in check_draft(d, "speaks few words", ctx.as_text())["issues"])


def test_recorded_measures_reach_the_prompt():
    from scribe.completeness import recorded_measures
    text = "Pauses and blocks.\nResult (SSI results): SSI-4 total 12, mild\nClarification (onset): 8 months"
    rec = recorded_measures(text)
    assert rec == ["SSI results: SSI-4 total 12, mild"]
    msg = build_draft_message(text, {}, "- facts", "None.", [], rec)
    assert "<recorded_measures>" in msg and "SSI-4 total 12" in msg.split("<recorded_measures>")[1]


def test_checklist_does_not_name_a_condition_the_clinician_did_not_name():
    items = " ".join(g["item"] + g["ask"] for g in gaps_for("Child has pauses and blocks when talking. Parents are worried."))
    assert "stammer" not in items.lower() and "disfluency" in items.lower()
    items = " ".join(g["item"] for g in gaps_for("Mother reports stammering at school."))
    assert "stammering" in items.lower()


def test_measure_names_stay_neutral_when_condition_not_named():
    from scribe.pipeline import neutral_condition_names

    def draft():
        return {"objective_suggestions": [{"measure": "Stuttering type profile",
                                           "method": "Code stuttering moments; count % syllables stuttered.",
                                           "why": "Shows the stammering pattern.", "record_as": "Stuttered ___"}],
                "soap": {"plan": [{"text": "Document stuttering types.", "origin": "ai_proposed", "rationale": ""},
                                  {"text": "Stuttering plan in clinician words.", "origin": "clinician", "rationale": ""}]}}
    d = draft()
    # A typed %SS result must not count as the clinician naming the condition.
    neutral_condition_names(d, "Boy with pauses and blocks.\nResult (Disfluency frequency): 7% syllables stuttered", "")
    m = d["objective_suggestions"][0]
    assert m["measure"] == "Disfluency type profile"
    assert m["method"] == "Code disfluency moments; count % syllables stuttered."
    assert "stammer" not in m["why"] and m["record_as"] == "Disfluency ___"
    assert d["soap"]["plan"][0]["text"] == "Document disfluency types."
    assert d["soap"]["plan"][1]["text"].startswith("Stuttering")      # clinician's words are never changed
    d = draft()
    neutral_condition_names(d, "Mother reports stammering.", "")
    assert d["objective_suggestions"][0]["measure"] == "Stuttering type profile"


def test_typed_ss_result_does_not_license_the_label():
    src = "Pauses and blocks.\nResult (Disfluency frequency): 7% syllables stuttered in conversation"
    assert "unsupported_term" in _soap_check("Stuttering was observed in conversation.", src)


def test_reworded_repeat_of_a_recorded_measure_is_dropped_but_next_level_kept():
    from scribe.completeness import drop_recorded_repeats
    recorded = ["Disfluency types and longest duration: part-word repetitions; longest 3 seconds",
                "Disfluency frequency % syllables stuttered in conversation: 7%",
                "/s/ accuracy at word level: 14/20"]
    sugg = [{"measure": "Disfluency types and longest disfluency moment in connected speech"},
            {"measure": "Disfluency frequency (% syllables stuttered) in picture description"},
            {"measure": "/s/ accuracy at sentence level"},
            {"measure": "Speech and language screening"}]
    kept = [s["measure"] for s in drop_recorded_repeats(sugg, recorded)]
    assert kept == ["Disfluency frequency (% syllables stuttered) in picture description",
                    "/s/ accuracy at sentence level", "Speech and language screening"]


def test_stuttered_syllables_wording_is_a_measure_name_not_the_condition():
    from scribe.completeness import names_condition
    assert not names_condition("Count stuttered syllables in a 300-syllable sample; 7% syllables stuttered; %SS 4")
    assert names_condition("Mother says he stutters at school")


def test_method_numbers_in_labelled_proposals_are_not_invented_data():
    d = {"analysis": {"findings": []}, "soap": {"plan": [
        {"text": "Take a sample in at least two speaking situations (300 syllables each).", "origin": "ai_proposed", "decision": "pending"},
        {"text": "Weekly sessions for 12 weeks.", "origin": "clinician", "basis": []}]}}
    issues = [i for i in check_draft(d, "pauses and blocks", "")["issues"] if i["type"] == "unsupported_number"]
    assert [i["ref"] for i in issues] == ["plan:1"]          # the clinician-labelled line is still checked


def test_short_blanks_are_normalised():
    from scribe.pipeline import blank_values
    assert blank_values("accuracy ___/___ (__%)") == "accuracy ___/___ (___%)"
