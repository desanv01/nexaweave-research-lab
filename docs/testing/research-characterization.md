# Research and document context characterization

These tests drive the inherited `ReportAgent` methods with a scripted LLM and a recording knowledge boundary. They do not instantiate live SDK clients, access a graph, conduct an OASIS interview, or evaluate the quality of model answers.

`test_research_workflow_contracts.py` exercises XML and bare JSON tool parsing, the four research dispatches, legacy dispatches, graph and simulation identifiers, explicit and fallback context, the historical selector, string limits, and returned errors. Section tests execute the real ReACT loop and inspect the messages sent to the scripted LLM: early final rejection, repeated research, conflicting tool and final output, fake tool-result removal, real observation injection, and bounded exhaustion. The chat test saves a synthetic report under a temporary report directory, then checks stored context truncation, ten-message history selection, one tool per iteration, 1,500-character observations, final cleanup, and the returned source list.

`sources` in chat are the `query` strings from executed tool calls. They are **not verified citations** or evidence that the query found a source. The fake knowledge service records dispatch, not retrieval correctness.

`test_long_document_contracts.py` calls the real ontology document context builder. Long fixtures place markers near the beginning, middle, and end and explicitly exceed the sampling threshold; multiple-document fixtures check selected document labels and the character budget. A sampled context can omit material in unselected chunks or documents. Unicode fixtures check Python character-bound handling, not byte or token limits, downstream model interpretation, or exact recall.

The section loop's forced-final response now strips fabricated `<tool_result>` content before extracting or returning the answer, matching normal finals. Focused regressions cover tagged, nested, and unclosed fabricated results. Remaining gaps from source inspection: the chat loop assumes non-`None` LLM responses, and XML-wrapped tool calls are parsed without the bare-JSON whitelist validation.

Execution and acceptance are reserved for Main; this worker did not run tests, lint, builds, or live calls.
