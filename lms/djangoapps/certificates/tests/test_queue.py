"""Tests for the XQueue certificates interface. """


import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import ddt
import freezegun
import pytz
from django.conf import settings
from django.test import TestCase
from django.test.utils import override_settings
from opaque_keys.edx.locator import CourseLocator
from testfixtures import LogCapture

# It is really unfortunate that we are using the XQueue client
# code from the capa library.  In the future, we should move this
# into a shared library.  We import it here so we can mock it
# and verify that items are being correctly added to the queue
# in our `XQueueCertInterface` implementation.
from capa.xqueue_interface import XQueueInterface
from common.djangoapps.course_modes import api as modes_api
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.certificates.models import (
    CertificateStatuses,
    ExampleCertificate,
    ExampleCertificateSet,
    GeneratedCertificate
)
from lms.djangoapps.certificates.queue import LOGGER, XQueueCertInterface
from lms.djangoapps.certificates.tests.factories import CertificateAllowlistFactory, GeneratedCertificateFactory
from lms.djangoapps.grades.tests.utils import mock_passing_grade
from lms.djangoapps.verify_student.tests.factories import SoftwareSecurePhotoVerificationFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


@ddt.ddt
@override_settings(CERT_QUEUE='certificates')
class XQueueCertInterfaceAddCertificateTest(ModuleStoreTestCase):
    """Test the "add to queue" operation of the XQueue interface. """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.course = CourseFactory.create()
        self.enrollment = CourseEnrollmentFactory(
            user=self.user,
            course_id=self.course.id,
            is_active=True,
            mode="honor",
        )
        self.xqueue = XQueueCertInterface()
        self.user_2 = UserFactory.create()
        SoftwareSecurePhotoVerificationFactory.create(user=self.user_2, status='approved')

    def test_add_cert_callback_url(self):

        with mock_passing_grade():
            with patch.object(XQueueInterface, 'send_to_queue') as mock_send:
                mock_send.return_value = (0, None)
                self.xqueue.add_cert(self.user, self.course.id)

        # Verify that the task was sent to the queue with the correct callback URL
        assert mock_send.called
        __, kwargs = mock_send.call_args_list[0]
        actual_header = json.loads(kwargs['header'])
        assert 'https://edx.org/update_certificate?key=' in actual_header['lms_callback_url']

    def test_no_create_action_in_queue_for_html_view_certs(self):
        """
        Tests there is no certificate create message in the queue if generate_pdf is False
        """
        with mock_passing_grade():
            with patch.object(XQueueInterface, 'send_to_queue') as mock_send:
                self.xqueue.add_cert(self.user, self.course.id, generate_pdf=False)

        # Verify that add_cert method does not add message to queue
        assert not mock_send.called
        certificate = GeneratedCertificate.eligible_certificates.get(user=self.user, course_id=self.course.id)
        assert certificate.status == CertificateStatuses.downloadable
        assert certificate.verify_uuid is not None

    @ddt.data('honor', 'audit')
    @override_settings(AUDIT_CERT_CUTOFF_DATE=datetime.now(pytz.UTC) - timedelta(days=1))
    def test_add_cert_with_honor_certificates(self, mode):
        """Test certificates generations for honor and audit modes."""
        template_name = 'certificate-template-{id.org}-{id.course}.pdf'.format(
            id=self.course.id
        )
        mock_send = self.add_cert_to_queue(mode)
        if modes_api.is_eligible_for_certificate(mode):
            self.assert_certificate_generated(mock_send, mode, template_name)
        else:
            self.assert_ineligible_certificate_generated(mock_send, mode)

    @ddt.data('credit', 'verified')
    def test_add_cert_with_verified_certificates(self, mode):
        """Test if enrollment mode is verified or credit along with valid
        software-secure verification than verified certificate should be generated.
        """
        template_name = 'certificate-template-{id.org}-{id.course}-verified.pdf'.format(
            id=self.course.id
        )

        mock_send = self.add_cert_to_queue(mode)
        self.assert_certificate_generated(mock_send, 'verified', template_name)

    @ddt.data((True, CertificateStatuses.audit_passing), (False, CertificateStatuses.generating))
    @ddt.unpack
    @override_settings(AUDIT_CERT_CUTOFF_DATE=datetime.now(pytz.UTC) - timedelta(days=1))
    def test_ineligible_cert_whitelisted(self, disable_audit_cert, status):
        """
        Test that audit mode students receive a certificate if DISABLE_AUDIT_CERTIFICATES
        feature is set to false
        """
        # Enroll as audit
        CourseEnrollmentFactory(
            user=self.user_2,
            course_id=self.course.id,
            is_active=True,
            mode='audit'
        )
        # Whitelist student
        CertificateAllowlistFactory(course_id=self.course.id, user=self.user_2)

        features = settings.FEATURES
        features['DISABLE_AUDIT_CERTIFICATES'] = disable_audit_cert
        with override_settings(FEATURES=features) and mock_passing_grade():
            with patch.object(XQueueInterface, 'send_to_queue') as mock_send:
                mock_send.return_value = (0, None)
                self.xqueue.add_cert(self.user_2, self.course.id)

        certificate = GeneratedCertificate.certificate_for_student(self.user_2, self.course.id)
        assert certificate is not None
        assert certificate.mode == 'audit'
        assert certificate.status == status

    def add_cert_to_queue(self, mode):
        """
        Dry method for course enrollment and adding request to
        queue. Returns a mock object containing information about the
        `XQueueInterface.send_to_queue` method, which can be used in other
        assertions.
        """
        CourseEnrollmentFactory(
            user=self.user_2,
            course_id=self.course.id,
            is_active=True,
            mode=mode,
        )
        with mock_passing_grade():
            with patch.object(XQueueInterface, 'send_to_queue') as mock_send:
                mock_send.return_value = (0, None)
                self.xqueue.add_cert(self.user_2, self.course.id)
                return mock_send

    def assert_certificate_generated(self, mock_send, expected_mode, expected_template_name):
        """
        Assert that a certificate was generated with the correct mode and
        template type.
        """
        # Verify that the task was sent to the queue with the correct callback URL
        assert mock_send.called
        __, kwargs = mock_send.call_args_list[0]

        actual_header = json.loads(kwargs['header'])
        assert 'https://edx.org/update_certificate?key=' in actual_header['lms_callback_url']

        body = json.loads(kwargs['body'])
        assert expected_template_name in body['template_pdf']

        certificate = GeneratedCertificate.eligible_certificates.get(user=self.user_2, course_id=self.course.id)
        assert certificate.mode == expected_mode

    def assert_ineligible_certificate_generated(self, mock_send, expected_mode):
        """
        Assert that an ineligible certificate was generated with the
        correct mode.
        """
        # Ensure the certificate was not generated
        assert not mock_send.called

        certificate = GeneratedCertificate.objects.get(
            user=self.user_2,
            course_id=self.course.id
        )

        assert certificate.status in (CertificateStatuses.audit_passing, CertificateStatuses.audit_notpassing)
        assert certificate.mode == expected_mode

    @ddt.data(
        (CertificateStatuses.restricted, False),
        (CertificateStatuses.deleting, False),
        (CertificateStatuses.generating, True),
        (CertificateStatuses.unavailable, True),
        (CertificateStatuses.deleted, True),
        (CertificateStatuses.error, True),
        (CertificateStatuses.notpassing, True),
        (CertificateStatuses.downloadable, True),
        (CertificateStatuses.auditing, True),
    )
    @ddt.unpack
    def test_add_cert_statuses(self, status, should_generate):
        """
        Test that certificates can or cannot be generated with the given
        certificate status.
        """
        with patch(
            'lms.djangoapps.certificates.queue.certificate_status_for_student',
            Mock(return_value={'status': status})
        ):
            mock_send = self.add_cert_to_queue('verified')
            if should_generate:
                assert mock_send.called
            else:
                assert not mock_send.called

    @ddt.data(
        # Eligible and should stay that way
        (
            CertificateStatuses.downloadable,
            timedelta(days=-2),
            'Pass',
            CertificateStatuses.generating
        ),
        # Ensure that certs in the wrong state can be fixed by regeneration
        (
            CertificateStatuses.downloadable,
            timedelta(hours=-1),
            'Pass',
            CertificateStatuses.audit_passing
        ),
        # Ineligible and should stay that way
        (
            CertificateStatuses.audit_passing,
            timedelta(hours=-1),
            'Pass',
            CertificateStatuses.audit_passing
        ),
        # As above
        (
            CertificateStatuses.audit_notpassing,
            timedelta(hours=-1),
            'Pass',
            CertificateStatuses.audit_passing
        ),
        # As above
        (
            CertificateStatuses.audit_notpassing,
            timedelta(hours=-1),
            None,
            CertificateStatuses.audit_notpassing
        ),
    )
    @ddt.unpack
    @override_settings(AUDIT_CERT_CUTOFF_DATE=datetime.now(pytz.UTC) - timedelta(days=1))
    def test_regen_audit_certs_eligibility(self, status, created_delta, grade, expected_status):
        """
        Test that existing audit certificates remain eligible even if cert
        generation is re-run.
        """
        # Create an existing audit enrollment and certificate
        CourseEnrollmentFactory(
            user=self.user_2,
            course_id=self.course.id,
            is_active=True,
            mode=CourseMode.AUDIT,
        )
        created_date = datetime.now(pytz.UTC) + created_delta
        with freezegun.freeze_time(created_date):
            GeneratedCertificateFactory(
                user=self.user_2,
                course_id=self.course.id,
                grade='1.0',
                status=status,
                mode=GeneratedCertificate.MODES.audit,
            )

        # Run grading/cert generation again
        with mock_passing_grade(letter_grade=grade):
            with patch.object(XQueueInterface, 'send_to_queue') as mock_send:
                mock_send.return_value = (0, None)
                self.xqueue.add_cert(self.user_2, self.course.id)

        assert GeneratedCertificate.objects.get(user=self.user_2, course_id=self.course.id).status == expected_status

    def test_regen_cert_with_pdf_certificate(self):
        """
        Test that regenerating a PDF certificate logs a warning message and the certificate
        status remains unchanged.
        """
        download_url = 'http://www.example.com/certificate.pdf'
        # Create an existing verified enrollment and certificate
        CourseEnrollmentFactory(
            user=self.user_2,
            course_id=self.course.id,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        GeneratedCertificateFactory(
            user=self.user_2,
            course_id=self.course.id,
            grade='1.0',
            status=CertificateStatuses.downloadable,
            mode=GeneratedCertificate.MODES.verified,
            download_url=download_url
        )

        self._assert_pdf_cert_generation_discontinued_logs(download_url)

    def test_add_cert_with_existing_pdf_certificate(self):
        """
        Test that adding a certificate for existing PDF certificates logs a  warning
        message and the certificate status remains unchanged.
        """
        download_url = 'http://www.example.com/certificate.pdf'
        # Create an existing verified enrollment and certificate
        CourseEnrollmentFactory(
            user=self.user_2,
            course_id=self.course.id,
            is_active=True,
            mode=CourseMode.VERIFIED,
        )
        GeneratedCertificateFactory(
            user=self.user_2,
            course_id=self.course.id,
            grade='1.0',
            status=CertificateStatuses.downloadable,
            mode=GeneratedCertificate.MODES.verified,
            download_url=download_url
        )

        self._assert_pdf_cert_generation_discontinued_logs(download_url, add_cert=True)

    def _assert_pdf_cert_generation_discontinued_logs(self, download_url, add_cert=False):
        """Assert PDF certificate generation discontinued logs."""
        with LogCapture(LOGGER.name) as log:
            if add_cert:
                self.xqueue.add_cert(self.user_2, self.course.id)
            else:
                self.xqueue.regen_cert(self.user_2, self.course.id)
            log.check_present(
                (
                    LOGGER.name,
                    'WARNING',
                    (
                        "PDF certificate generation discontinued, canceling "
                        "PDF certificate generation for student {student_id} "
                        "in course '{course_id}' "
                        "with status '{status}' "
                        "and download_url '{download_url}'."
                    ).format(
                        student_id=self.user_2.id,
                        course_id=str(self.course.id),
                        status=CertificateStatuses.downloadable,
                        download_url=download_url
                    )
                )
            )


@override_settings(CERT_QUEUE='certificates')
class XQueueCertInterfaceExampleCertificateTest(TestCase):
    """Tests for the XQueue interface for certificate generation. """

    COURSE_KEY = CourseLocator(org='test', course='test', run='test')

    TEMPLATE = 'test.pdf'
    DESCRIPTION = 'test'
    ERROR_MSG = 'Kaboom!'

    def setUp(self):
        super().setUp()
        self.xqueue = XQueueCertInterface()

    def test_add_example_cert(self):
        cert = self._create_example_cert()
        with self._mock_xqueue() as mock_send:
            self.xqueue.add_example_cert(cert)

        # Verify that the correct payload was sent to the XQueue
        self._assert_queue_task(mock_send, cert)

        # Verify the certificate status
        assert cert.status == ExampleCertificate.STATUS_STARTED

    def test_add_example_cert_error(self):
        cert = self._create_example_cert()
        with self._mock_xqueue(success=False):
            self.xqueue.add_example_cert(cert)

        # Verify the error status of the certificate
        assert cert.status == ExampleCertificate.STATUS_ERROR
        assert self.ERROR_MSG in cert.error_reason

    def _create_example_cert(self):
        """Create an example certificate. """
        cert_set = ExampleCertificateSet.objects.create(course_key=self.COURSE_KEY)
        return ExampleCertificate.objects.create(
            example_cert_set=cert_set,
            description=self.DESCRIPTION,
            template=self.TEMPLATE
        )

    @contextmanager
    def _mock_xqueue(self, success=True):
        """Mock the XQueue method for sending a task to the queue. """
        with patch.object(XQueueInterface, 'send_to_queue') as mock_send:
            mock_send.return_value = (0, None) if success else (1, self.ERROR_MSG)
            yield mock_send

    def _assert_queue_task(self, mock_send, cert):
        """Check that the task was added to the queue. """
        expected_header = {
            'lms_key': cert.access_key,
            'lms_callback_url': f'https://edx.org/update_example_certificate?key={cert.uuid}',
            'queue_name': 'certificates'
        }

        expected_body = {
            'action': 'create',
            'username': cert.uuid,
            'name': 'John Doë',
            'course_id': str(self.COURSE_KEY),
            'template_pdf': 'test.pdf',
            'example_certificate': True
        }

        assert mock_send.called

        __, kwargs = mock_send.call_args_list[0]
        actual_header = json.loads(kwargs['header'])
        actual_body = json.loads(kwargs['body'])

        assert expected_header == actual_header
        assert expected_body == actual_body
