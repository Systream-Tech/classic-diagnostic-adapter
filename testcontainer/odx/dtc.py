#!/usr/bin/env python3
# Copyright (c) 2025 The Contributors to Eclipse OpenSOVD (see CONTRIBUTORS)
#
# See the NOTICE file(s) distributed with this work for additional
# information regarding copyright ownership.
#
# This program and the accompanying materials are made available under the
# terms of the Apache License Version 2.0 which is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# SPDX-License-Identifier: Apache-2.0

from odxtools.dataobjectproperty import DataObjectProperty
from odxtools.diaglayers.diaglayerraw import DiagLayerRaw
from odxtools.diagservice import DiagService
from odxtools.diagnostictroublecode import DiagnosticTroubleCode
from odxtools.dtcdop import DtcDop
from odxtools.minmaxlengthtype import MinMaxLengthType
from odxtools.nameditemlist import NamedItemList
from odxtools.odxtypes import DataType
from odxtools.parameters.valueparameter import ValueParameter
from odxtools.physicaltype import PhysicalType
from odxtools.request import Request
from odxtools.response import Response, ResponseType
from odxtools.standardlengthtype import StandardLengthType
from odxtools.termination import Termination
from odxtools.compumethods.compucategory import CompuCategory
from odxtools.compumethods.compumethod import CompuMethod
from odxtools.text import Text

from helper import (
    derived_id,
    sid_parameter_rq,
    sid_parameter_pr,
    subfunction_rq,
    matching_request_parameter_subfunction,
    functional_class_ref,
    ref,
    find_dop_by_shortname,
)


def _ensure_dtc_dops(dlr: DiagLayerRaw):
    """
    Ensure DOPs required for ReadDTCInformation services exist.

    We keep these fairly generic so they can be reused by multiple services:
    - DTC_24Bit: DTC-DOP with pre-defined DTCs for the ECU
    - DTC_SnapshotData: BYTEFIELD until end-of-PDU for snapshot records
    - DTC_ExtendedData: BYTEFIELD until end-of-PDU for extended data records
    """
    existing = {dop.short_name: dop for dop in dlr.diag_data_dictionary_spec.data_object_props}
    existing_dtc_dops = {dop.short_name: dop for dop in dlr.diag_data_dictionary_spec.dtc_dops}

    dtc_24 = existing_dtc_dops.get("DTC_24Bit")
    if dtc_24 is None:
        # Pre-define the DTCs that the ECU-sim can report.
        # CDA requires DTCs to be declared in the DTC-DOP to parse them from responses.
        test_dtc = DiagnosticTroubleCode(
            odx_id=derived_id(dlr, "DTC.TestDTC_123456"),
            short_name="TestDTC_123456",
            trouble_code=0x123456,  # DTC code as returned by ECU-sim
            text=Text(text="Test DTC for ECU simulator"),
            display_trouble_code="P123456",
            level=1,  # Severity level
        )

        dtc_24 = DtcDop(
            odx_id=derived_id(dlr, "DOP.DTC_24Bit"),
            short_name="DTC_24Bit",
            compu_method=CompuMethod(
                category=CompuCategory.IDENTICAL,
                physical_type=DataType.A_UINT32,
                internal_type=DataType.A_UINT32,
            ),
            physical_type=PhysicalType(base_data_type=DataType.A_UINT32),
            diag_coded_type=StandardLengthType(
                base_data_type=DataType.A_UINT32,
                bit_length=24,
            ),
            dtcs_raw=[test_dtc],
        )
        dlr.diag_data_dictionary_spec.dtc_dops.append(dtc_24)

    snapshot = existing.get("DTC_SnapshotData")
    if snapshot is None:
        snapshot = DataObjectProperty(
            odx_id=derived_id(dlr, "DOP.DTC_SnapshotData"),
            short_name="DTC_SnapshotData",
            compu_method=CompuMethod(
                category=CompuCategory.IDENTICAL,
                physical_type=DataType.A_BYTEFIELD,
                internal_type=DataType.A_BYTEFIELD,
            ),
            physical_type=PhysicalType(base_data_type=DataType.A_BYTEFIELD),
            diag_coded_type=MinMaxLengthType(
                base_data_type=DataType.A_BYTEFIELD,
                min_length=0,
                max_length=4_000_000,
                termination=Termination.END_OF_PDU,
            ),
        )
        dlr.diag_data_dictionary_spec.data_object_props.append(snapshot)

    extended = existing.get("DTC_ExtendedData")
    if extended is None:
        extended = DataObjectProperty(
            odx_id=derived_id(dlr, "DOP.DTC_ExtendedData"),
            short_name="DTC_ExtendedData",
            compu_method=CompuMethod(
                category=CompuCategory.IDENTICAL,
                physical_type=DataType.A_BYTEFIELD,
                internal_type=DataType.A_BYTEFIELD,
            ),
            physical_type=PhysicalType(base_data_type=DataType.A_BYTEFIELD),
            diag_coded_type=MinMaxLengthType(
                base_data_type=DataType.A_BYTEFIELD,
                min_length=0,
                max_length=4_000_000,
                termination=Termination.END_OF_PDU,
            ),
        )
        dlr.diag_data_dictionary_spec.data_object_props.append(extended)

    # Reuse existing IDENTICAL_UINT_8 as 1-byte status / record number where needed
    status_dop = find_dop_by_shortname(dlr, "IDENTICAL_UINT_8")

    return dtc_24, snapshot, extended, status_dop


def _add_read_dtc_snapshot_by_dtc_number(dlr: DiagLayerRaw):
    """
    0x19 0x04 - ReadDTCInformation: ReportDTC snapshot record by DTC number.

    Request layout (ISO 14229-1):
      - SID            (0) : 0x19
      - SubFunction    (1) : 0x04
      - DTC            (2..4) : 24-bit DTC
      - RecordNumber   (5) : Snapshot record number

    Positive response (0x59 0x04):
      - SID            (0) : 0x59
      - DTC            (1..3)
      - StatusMask     (4)
      - SnapshotData   (5..n)
    """
    dtc_24, snapshot_dop, _extended_dop, status_dop = _ensure_dtc_dops(dlr)

    # Request
    request = Request(
        odx_id=derived_id(dlr, "RQ.RQ_ReadDTCInformation_ReportDTCSnapshotRecordByDTCNbr"),
        short_name="RQ_ReadDTCInformation_ReportDTCSnapshotRecordByDTCNbr",
        parameters=NamedItemList(
            [
                sid_parameter_rq(0x19),
                subfunction_rq(0x04),
                ValueParameter(
                    short_name="DTC",
                    semantic="DATA",
                    byte_position=2,
                    dop_ref=ref(dtc_24),
                ),
                ValueParameter(
                    short_name="RecordNumber",
                    semantic="RECORD-NUMBER",
                    byte_position=5,
                    dop_ref=ref(status_dop),
                ),
            ]
        ),
    )
    dlr.requests.append(request)

    # Positive response
    response = Response(
        response_type=ResponseType.POSITIVE,
        odx_id=derived_id(dlr, "PR.PR_ReadDTCInformation_ReportDTCSnapshotRecordByDTCNbr"),
        short_name="PR_ReadDTCInformation_ReportDTCSnapshotRecordByDTCNbr",
        parameters=NamedItemList(
            [
                sid_parameter_pr(0x19 + 0x40),
                ValueParameter(
                    short_name="DTC",
                    semantic="DATA",
                    byte_position=1,
                    dop_ref=ref(dtc_24),
                ),
                ValueParameter(
                    short_name="StatusMask",
                    semantic="DATA",
                    byte_position=4,
                    dop_ref=ref(status_dop),
                ),
                ValueParameter(
                    short_name="SnapshotRecord",
                    semantic="DATA",
                    byte_position=5,
                    dop_ref=ref(snapshot_dop),
                ),
            ]
        ),
    )
    dlr.positive_responses.append(response)

    service = DiagService(
        odx_id=derived_id(dlr, "DC.ReadDTCInformation_ReportDTCSnapshotRecordByDTCNbr"),
        short_name="ReadDTCInformation_ReportDTCSnapshotRecordByDTCNbr",
        functional_class_refs=[functional_class_ref(dlr, "DtcInformation")],
        request_ref=ref(request),
        pos_response_refs=[ref(response)],
    )
    dlr.diag_comms_raw.append(service)


def _add_read_dtc_by_status_mask(dlr: DiagLayerRaw):
    """
    0x19 0x02 - ReadDTCInformation: Report DTC by status mask.

    This is the primary service CDA uses to decide whether an ECU
    "supports fault memory" for the `/faults` endpoint
    (DtcReadInformationFunction::FaultMemoryByStatusMask).

    Request layout:
      - SID            (0) : 0x19
      - SubFunction    (1) : 0x02
      - StatusMask     (2) : 1 byte status mask

    Positive response (0x59 0x02):
      - SID            (0) : 0x59
      - AvailabilityMask (1)
      - DtcFormatIdentifier (2)
      - Records (3..n) : sequence of [DTC (3 bytes) + StatusMask (1 byte)]
    """
    dtc_24, _snapshot_dop, _extended_dop, status_dop = _ensure_dtc_dops(dlr)

    # Request: 19 02 [StatusMask]
    request = Request(
        odx_id=derived_id(dlr, "RQ.RQ_ReadDTCInformation_DTCByStatusMask"),
        short_name="RQ_ReadDTCInformation_DTCByStatusMask",
        parameters=NamedItemList(
            [
                sid_parameter_rq(0x19),
                subfunction_rq(0x02),
                ValueParameter(
                    short_name="StatusMask",
                    semantic="STATUS-MASK",
                    byte_position=2,
                    dop_ref=ref(status_dop),
                ),
            ]
        ),
    )
    dlr.requests.append(request)

    # Positive response format for 0x19 02 (ISO 14229-1):
    #   Byte 0: SID (0x59)
    #   Byte 1: reportType (0x02) - subfunction echo
    #   Byte 2: DTCStatusAvailabilityMask
    #   Bytes 3-5: DTC (24-bit)
    #   Byte 6: statusOfDTC
    #   ... (repeating DTC+status records)
    response = Response(
        response_type=ResponseType.POSITIVE,
        odx_id=derived_id(dlr, "PR.PR_ReadDTCInformation_DTCByStatusMask"),
        short_name="PR_ReadDTCInformation_DTCByStatusMask",
        parameters=NamedItemList(
            [
                sid_parameter_pr(0x19 + 0x40),
                # Subfunction echo at byte 1
                matching_request_parameter_subfunction(
                    short_name="ReportType",
                    byte_position=1,
                    request_byte_position=1,
                ),
                # DTCStatusAvailabilityMask at byte 2
                ValueParameter(
                    short_name="DTCStatusAvailabilityMask",
                    semantic="AVAILABILITY-STATUS",
                    byte_position=2,
                    dop_ref=ref(status_dop),
                ),
                # First DTC (24-bit) at bytes 3-5; CDA uses this to infer DTC bit positions.
                # NOTE: semantic must be "DATA" for CDA to process this parameter during response mapping
                ValueParameter(
                    short_name="DTC",
                    semantic="DATA",
                    byte_position=3,
                    dop_ref=ref(dtc_24),
                ),
                # Status mask associated with that DTC at byte 6
                ValueParameter(
                    short_name="StatusOfDTC",
                    semantic="STATUS",
                    byte_position=6,
                    dop_ref=ref(status_dop),
                ),
            ]
        ),
    )
    dlr.positive_responses.append(response)

    service = DiagService(
        odx_id=derived_id(dlr, "DC.ReadDTCInformation_DTCByStatusMask"),
        short_name="ReadDTCInformation_DTCByStatusMask",
        functional_class_refs=[functional_class_ref(dlr, "DtcInformation")],
        request_ref=ref(request),
        pos_response_refs=[ref(response)],
    )
    dlr.diag_comms_raw.append(service)


def _add_read_dtc_extended_by_dtc_number(dlr: DiagLayerRaw):
    """
    0x19 0x06 - ReadDTCInformation: ReportDTC extended data record by DTC number.

    Request layout:
      - SID            (0) : 0x19
      - SubFunction    (1) : 0x06
      - DTC            (2..4) : 24-bit DTC
      - RecordNumber   (5) : Extended data record number

    Positive response (0x59 0x06):
      - SID            (0) : 0x59
      - DTC            (1..3)
      - StatusMask     (4)
      - ExtendedData   (5..n)
    """
    dtc_24, _snapshot_dop, extended_dop, status_dop = _ensure_dtc_dops(dlr)

    # Request
    request = Request(
        odx_id=derived_id(dlr, "RQ.RQ_ReadDTCInformation_ReportDTCExtendedDataByDTCNbr"),
        short_name="RQ_ReadDTCInformation_ReportDTCExtendedDataByDTCNbr",
        parameters=NamedItemList(
            [
                sid_parameter_rq(0x19),
                subfunction_rq(0x06),
                ValueParameter(
                    short_name="DTC",
                    semantic="DATA",
                    byte_position=2,
                    dop_ref=ref(dtc_24),
                ),
                ValueParameter(
                    short_name="RecordNumber",
                    semantic="RECORD-NUMBER",
                    byte_position=5,
                    dop_ref=ref(status_dop),
                ),
            ]
        ),
    )
    dlr.requests.append(request)

    # Positive response
    response = Response(
        response_type=ResponseType.POSITIVE,
        odx_id=derived_id(dlr, "PR.PR_ReadDTCInformation_ReportDTCExtendedDataByDTCNbr"),
        short_name="PR_ReadDTCInformation_ReportDTCExtendedDataByDTCNbr",
        parameters=NamedItemList(
            [
                sid_parameter_pr(0x19 + 0x40),
                ValueParameter(
                    short_name="DTC",
                    semantic="DATA",
                    byte_position=1,
                    dop_ref=ref(dtc_24),
                ),
                ValueParameter(
                    short_name="StatusMask",
                    semantic="DATA",
                    byte_position=4,
                    dop_ref=ref(status_dop),
                ),
                ValueParameter(
                    short_name="ExtendedData",
                    semantic="DATA",
                    byte_position=5,
                    dop_ref=ref(extended_dop),
                ),
            ]
        ),
    )
    dlr.positive_responses.append(response)

    service = DiagService(
        odx_id=derived_id(dlr, "DC.ReadDTCInformation_ReportDTCExtendedDataByDTCNbr"),
        short_name="ReadDTCInformation_ReportDTCExtendedDataByDTCNbr",
        functional_class_refs=[functional_class_ref(dlr, "DtcInformation")],
        request_ref=ref(request),
        pos_response_refs=[ref(response)],
    )
    dlr.diag_comms_raw.append(service)


def add_dtc_services(dlr: DiagLayerRaw):
    """
    Add UDS service 0x19 (ReadDTCInformation) variants needed by the test container:
      - 0x19 0x02: Report DTC by status mask   (FaultMemoryByStatusMask)
      - 0x19 0x04: Report DTC snapshot record by DTC number
      - 0x19 0x06: Report DTC extended data record by DTC number

    The ECU simulator already implements these subfunctions; this function
    adds matching ODX service definitions so that the generated PDX is
    compatible with the CDA test container behaviour.
    """
    _add_read_dtc_by_status_mask(dlr)
    _add_read_dtc_snapshot_by_dtc_number(dlr)
    _add_read_dtc_extended_by_dtc_number(dlr)

